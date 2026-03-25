"""Phase 1: AST parsing, incremental detection, and cAST chunking."""

import ast
import hashlib
import os
import sys
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import click
import tree_sitter_java as tsjava
import tree_sitter_kotlin as tskotlin
import tree_sitter_ruby as tsruby
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Parser as TSParser


def _estimate_tokens(text: str) -> float:
    """Estimate token count: len(split()) * 1.3."""
    return len(text.split()) * 1.3


def _sha256(text: str) -> str:
    """Return SHA-256 hex digest of text."""
    return hashlib.sha256(text.encode()).hexdigest()


def _get_source_segment(source_lines: list[str], start_line: int, end_line: int) -> str:
    """Extract source lines (1-based inclusive)."""
    return "\n".join(source_lines[start_line - 1 : end_line])


def _get_signature(node: ast.AST) -> str | None:
    """Extract function/method signature from AST node."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = ast.dump(node.args)
        # Build a readable signature
        parts = []
        for arg in node.args.args:
            ann = ""
            if arg.annotation:
                ann = f": {ast.unparse(arg.annotation)}"
            parts.append(f"{arg.arg}{ann}")
        returns = ""
        if node.returns:
            returns = f" -> {ast.unparse(node.returns)}"
        return f"({', '.join(parts)}){returns}"
    return None


def _get_docstring(node: ast.AST) -> str | None:
    """Extract docstring from a class or function node."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return ast.get_docstring(node)
    return None


def _load_gitignore_patterns(repo_root: Path) -> list[str]:
    """Load .gitignore patterns from repo root."""
    gitignore = repo_root / ".gitignore"
    patterns = []
    if gitignore.exists():
        for line in gitignore.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    return patterns


def _is_ignored(file_path: Path, repo_root: Path, gitignore_patterns: list[str]) -> bool:
    """Check if a file should be ignored based on .gitignore patterns and built-in rules."""
    rel = file_path.relative_to(repo_root)
    rel_str = str(rel)
    rel_posix = rel.as_posix()

    # Always exclude .codeindex/*.db
    if ".codeindex" in rel.parts:
        return True

    for pattern in gitignore_patterns:
        # Directory pattern (ends with /)
        if pattern.endswith("/"):
            dir_name = pattern.rstrip("/")
            if dir_name in rel.parts:
                return True
        else:
            # File pattern
            if fnmatch(rel_posix, pattern) or fnmatch(file_path.name, pattern):
                return True
            # Also check each path component for directory matches
            for part in rel.parts:
                if fnmatch(part, pattern):
                    return True

    return False


def _file_content_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of file contents."""
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".kt": "kotlin",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".rb": "ruby",
}

_UNSUPPORTED_EXTENSIONS: dict[str, str] = {
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
}


def detect_language(path: Path) -> str | None:
    """Detect language from file extension.

    Returns language name string, or None for unsupported extensions.
    Logs a warning for known-but-unsupported languages.
    """
    ext = path.suffix.lower()
    lang = _EXTENSION_TO_LANGUAGE.get(ext)
    if lang:
        return lang
    unsupported = _UNSUPPORTED_EXTENSIONS.get(ext)
    if unsupported:
        click.echo(
            f"[WARNING] Unsupported language: {unsupported}, skipping {path}",
            err=True,
        )
        return None
    return None


# ---------------------------------------------------------------------------
# tree-sitter language setup (lazy singletons)
# ---------------------------------------------------------------------------

_KT_LANGUAGE: Language | None = None
_TS_LANGUAGE: Language | None = None
_TSX_LANGUAGE: Language | None = None
_JAVA_LANGUAGE: Language | None = None
_RUBY_LANGUAGE: Language | None = None


def _get_kotlin_language() -> Language:
    global _KT_LANGUAGE
    if _KT_LANGUAGE is None:
        _KT_LANGUAGE = Language(tskotlin.language())
    return _KT_LANGUAGE


def _get_typescript_language() -> Language:
    global _TS_LANGUAGE
    if _TS_LANGUAGE is None:
        _TS_LANGUAGE = Language(tstypescript.language_typescript())
    return _TS_LANGUAGE


def _get_tsx_language() -> Language:
    global _TSX_LANGUAGE
    if _TSX_LANGUAGE is None:
        _TSX_LANGUAGE = Language(tstypescript.language_tsx())
    return _TSX_LANGUAGE


def _get_java_language() -> Language:
    global _JAVA_LANGUAGE
    if _JAVA_LANGUAGE is None:
        _JAVA_LANGUAGE = Language(tsjava.language())
    return _JAVA_LANGUAGE


def _get_ruby_language() -> Language:
    global _RUBY_LANGUAGE
    if _RUBY_LANGUAGE is None:
        _RUBY_LANGUAGE = Language(tsruby.language())
    return _RUBY_LANGUAGE


# ---------------------------------------------------------------------------
# tree-sitter based parsing (Kotlin & TypeScript)
# ---------------------------------------------------------------------------


def _ts_get_name(node) -> str | None:
    """Extract name from a tree-sitter node using field or child heuristics."""
    # Try common field names
    for field in ("name",):
        child = node.child_by_field_name(field)
        if child:
            return child.text.decode("utf8")
    # For Kotlin/TS, the identifier child is usually the name
    for child in node.children:
        if child.type in ("identifier", "type_identifier", "property_identifier"):
            return child.text.decode("utf8")
    return None


def _ts_get_signature_kotlin(node, source: str) -> str | None:
    """Extract function signature from a Kotlin function_declaration node."""
    params_node = node.child_by_field_name("value_parameters")
    if params_node is None:
        # Try searching children
        for child in node.children:
            if child.type == "function_value_parameters":
                params_node = child
                break
    if params_node is None:
        return None
    params_text = source[params_node.start_byte : params_node.end_byte]
    # Check for return type
    ret_type = node.child_by_field_name("type")
    if ret_type is None:
        # Look for user_type child after parameters
        found_params = False
        for child in node.children:
            if child == params_node:
                found_params = True
                continue
            if found_params and child.type in ("user_type", "nullable_type", "function_type"):
                ret_text = source[child.start_byte : child.end_byte]
                return f"{params_text}: {ret_text}"
    else:
        ret_text = source[ret_type.start_byte : ret_type.end_byte]
        return f"{params_text}: {ret_text}"
    return params_text


def _ts_get_signature_typescript(node, source: str) -> str | None:
    """Extract function signature from a TypeScript method/function node."""
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        for child in node.children:
            if child.type == "formal_parameters":
                params_node = child
                break
    if params_node is None:
        return None
    params_text = source[params_node.start_byte : params_node.end_byte]
    # Check for return type annotation
    ret_type = node.child_by_field_name("return_type")
    if ret_type:
        ret_text = source[ret_type.start_byte : ret_type.end_byte]
        return f"{params_text}{ret_text}"
    # Look for type_annotation after parameters
    found_params = False
    for child in node.children:
        if child == params_node:
            found_params = True
            continue
        if found_params and child.type == "type_annotation":
            ret_text = source[child.start_byte : child.end_byte]
            return f"{params_text}{ret_text}"
    return params_text


def _ts_get_docstring(node, source: str) -> str | None:
    """Extract preceding block comment as docstring (JSDoc / KDoc style)."""
    prev = node.prev_named_sibling
    if prev and prev.type in ("comment", "block_comment", "multiline_comment"):
        text = source[prev.start_byte : prev.end_byte]
        if text.startswith("/**"):
            # Strip comment markers
            lines = text.splitlines()
            cleaned = []
            for line in lines:
                line = line.strip()
                if line in ("/**", "*/"):
                    continue
                if line.startswith("* "):
                    cleaned.append(line[2:])
                elif line.startswith("*"):
                    cleaned.append(line[1:].lstrip())
                else:
                    cleaned.append(line)
            return "\n".join(cleaned).strip() or None
    return None


def _parse_kotlin_file(
    path: Path,
    repo_root: Path,
    source: str,
    token_limit: int = 512,
) -> list[dict[str, Any]]:
    """Parse a Kotlin file using tree-sitter and extract nodes."""
    rel_path = path.relative_to(repo_root).as_posix()
    source_lines = source.splitlines()
    lang = _get_kotlin_language()
    parser = TSParser(lang)
    tree = parser.parse(bytes(source, "utf8"))
    root = tree.root_node

    nodes: list[dict[str, Any]] = []

    # File node
    file_hash = _sha256(source)
    nodes.append({
        "id": f"{rel_path}::file::{rel_path}",
        "file_path": rel_path,
        "node_type": "file",
        "name": path.name,
        "qualified_name": rel_path,
        "signature": None,
        "docstring": None,
        "start_line": 1,
        "end_line": len(source_lines),
        "language": "kotlin",
        "raw_source": source,
        "content_hash": file_hash,
    })

    def _extract_kotlin_nodes(parent_node, class_name: str | None = None):
        """Recursively extract nodes from Kotlin AST."""
        for child in parent_node.children:
            if child.type == "class_declaration":
                # Determine if interface
                is_interface = False
                for c in child.children:
                    if c.type == "interface" or (not c.is_named and c.text == b"interface"):
                        is_interface = True
                        break
                name = _ts_get_name(child)
                if not name:
                    continue
                node_type = "interface" if is_interface else "class"
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                raw = _get_source_segment(source_lines, start_line, end_line)
                docstring = _ts_get_docstring(child, source)
                qname = f"{class_name}.{name}" if class_name else name
                nodes.append({
                    "id": f"{rel_path}::{node_type}::{qname}",
                    "file_path": rel_path,
                    "node_type": node_type,
                    "name": name,
                    "qualified_name": qname,
                    "signature": None,
                    "docstring": docstring,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": "kotlin",
                    "raw_source": raw,
                    "content_hash": _sha256(raw),
                })
                # Recurse into class body for methods
                for body_child in child.children:
                    if body_child.type == "class_body":
                        _extract_kotlin_nodes(body_child, name)

            elif child.type == "object_declaration":
                name = _ts_get_name(child)
                if not name:
                    continue
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                raw = _get_source_segment(source_lines, start_line, end_line)
                docstring = _ts_get_docstring(child, source)
                qname = f"{class_name}.{name}" if class_name else name
                nodes.append({
                    "id": f"{rel_path}::object::{qname}",
                    "file_path": rel_path,
                    "node_type": "object",
                    "name": name,
                    "qualified_name": qname,
                    "signature": None,
                    "docstring": docstring,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": "kotlin",
                    "raw_source": raw,
                    "content_hash": _sha256(raw),
                })
                # Recurse into object body for methods
                for body_child in child.children:
                    if body_child.type == "class_body":
                        _extract_kotlin_nodes(body_child, name)

            elif child.type == "function_declaration":
                name = _ts_get_name(child)
                if not name:
                    continue
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                raw = _get_source_segment(source_lines, start_line, end_line)
                sig = _ts_get_signature_kotlin(child, source)
                docstring = _ts_get_docstring(child, source)
                if class_name:
                    node_type = "method"
                    qname = f"{class_name}.{name}"
                else:
                    node_type = "function"
                    qname = name
                node_dict = {
                    "id": f"{rel_path}::{node_type}::{qname}",
                    "file_path": rel_path,
                    "node_type": node_type,
                    "name": name,
                    "qualified_name": qname,
                    "signature": sig,
                    "docstring": docstring,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": "kotlin",
                    "raw_source": raw,
                    "content_hash": _sha256(raw),
                }
                # Apply cAST chunking via token estimate
                if _estimate_tokens(raw) > token_limit:
                    nodes.append(node_dict)
                    _chunk_treesitter_node(node_dict, source_lines, token_limit, child, nodes)
                else:
                    nodes.append(node_dict)

    _extract_kotlin_nodes(root)
    return nodes


def _parse_typescript_file(
    path: Path,
    repo_root: Path,
    source: str,
    token_limit: int = 512,
) -> list[dict[str, Any]]:
    """Parse a TypeScript file using tree-sitter and extract nodes."""
    rel_path = path.relative_to(repo_root).as_posix()
    source_lines = source.splitlines()

    if path.suffix.lower() == ".tsx":
        lang = _get_tsx_language()
    else:
        lang = _get_typescript_language()
    parser = TSParser(lang)
    tree = parser.parse(bytes(source, "utf8"))
    root = tree.root_node

    nodes: list[dict[str, Any]] = []

    # File node
    file_hash = _sha256(source)
    nodes.append({
        "id": f"{rel_path}::file::{rel_path}",
        "file_path": rel_path,
        "node_type": "file",
        "name": path.name,
        "qualified_name": rel_path,
        "signature": None,
        "docstring": None,
        "start_line": 1,
        "end_line": len(source_lines),
        "language": "typescript",
        "raw_source": source,
        "content_hash": file_hash,
    })

    def _extract_ts_nodes(parent_node, class_name: str | None = None):
        """Recursively extract nodes from TypeScript AST."""
        for child in parent_node.children:
            if child.type == "class_declaration":
                name = _ts_get_name(child)
                if not name:
                    continue
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                raw = _get_source_segment(source_lines, start_line, end_line)
                docstring = _ts_get_docstring(child, source)
                qname = f"{class_name}.{name}" if class_name else name
                nodes.append({
                    "id": f"{rel_path}::class::{qname}",
                    "file_path": rel_path,
                    "node_type": "class",
                    "name": name,
                    "qualified_name": qname,
                    "signature": None,
                    "docstring": docstring,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": "typescript",
                    "raw_source": raw,
                    "content_hash": _sha256(raw),
                })
                # Recurse into class body for methods
                for body_child in child.children:
                    if body_child.type == "class_body":
                        _extract_ts_nodes(body_child, name)

            elif child.type == "interface_declaration":
                name = _ts_get_name(child)
                if not name:
                    continue
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                raw = _get_source_segment(source_lines, start_line, end_line)
                docstring = _ts_get_docstring(child, source)
                qname = f"{class_name}.{name}" if class_name else name
                nodes.append({
                    "id": f"{rel_path}::interface::{qname}",
                    "file_path": rel_path,
                    "node_type": "interface",
                    "name": name,
                    "qualified_name": qname,
                    "signature": None,
                    "docstring": docstring,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": "typescript",
                    "raw_source": raw,
                    "content_hash": _sha256(raw),
                })
                # Recurse into interface body for method signatures
                for body_child in child.children:
                    if body_child.type == "interface_body":
                        _extract_ts_methods_from_interface(body_child, name, rel_path, source, source_lines, token_limit, nodes)

            elif child.type == "function_declaration":
                name = _ts_get_name(child)
                if not name:
                    continue
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                raw = _get_source_segment(source_lines, start_line, end_line)
                sig = _ts_get_signature_typescript(child, source)
                docstring = _ts_get_docstring(child, source)
                if class_name:
                    node_type = "method"
                    qname = f"{class_name}.{name}"
                else:
                    node_type = "function"
                    qname = name
                node_dict = {
                    "id": f"{rel_path}::{node_type}::{qname}",
                    "file_path": rel_path,
                    "node_type": node_type,
                    "name": name,
                    "qualified_name": qname,
                    "signature": sig,
                    "docstring": docstring,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": "typescript",
                    "raw_source": raw,
                    "content_hash": _sha256(raw),
                }
                if _estimate_tokens(raw) > token_limit:
                    nodes.append(node_dict)
                    _chunk_treesitter_node(node_dict, source_lines, token_limit, child, nodes)
                else:
                    nodes.append(node_dict)

            elif child.type in ("method_definition", "method_signature"):
                if class_name:
                    name = _ts_get_name(child)
                    if not name:
                        continue
                    start_line = child.start_point[0] + 1
                    end_line = child.end_point[0] + 1
                    raw = _get_source_segment(source_lines, start_line, end_line)
                    sig = _ts_get_signature_typescript(child, source)
                    docstring = _ts_get_docstring(child, source)
                    qname = f"{class_name}.{name}"
                    node_dict = {
                        "id": f"{rel_path}::method::{qname}",
                        "file_path": rel_path,
                        "node_type": "method",
                        "name": name,
                        "qualified_name": qname,
                        "signature": sig,
                        "docstring": docstring,
                        "start_line": start_line,
                        "end_line": end_line,
                        "language": "typescript",
                        "raw_source": raw,
                        "content_hash": _sha256(raw),
                    }
                    if _estimate_tokens(raw) > token_limit:
                        nodes.append(node_dict)
                        _chunk_treesitter_node(node_dict, source_lines, token_limit, child, nodes)
                    else:
                        nodes.append(node_dict)

    _extract_ts_nodes(root)
    return nodes


def _java_get_signature(node, source: str) -> str | None:
    """Extract method/constructor signature from a Java method_declaration or constructor_declaration."""
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return None
    params_text = source[params_node.start_byte : params_node.end_byte]
    ret_type = node.child_by_field_name("type")
    if ret_type:
        ret_text = source[ret_type.start_byte : ret_type.end_byte]
        return f"{params_text}: {ret_text}"
    return params_text


def _java_get_docstring(node, source: str) -> str | None:
    """Extract Javadoc comment preceding a node."""
    prev = node.prev_named_sibling
    if prev and prev.type == "block_comment":
        text = source[prev.start_byte : prev.end_byte]
        if text.startswith("/**"):
            lines = text.splitlines()
            cleaned = []
            for line in lines:
                line = line.strip()
                if line in ("/**", "*/"):
                    continue
                if line.startswith("* "):
                    cleaned.append(line[2:])
                elif line.startswith("*"):
                    cleaned.append(line[1:].lstrip())
                else:
                    cleaned.append(line)
            return "\n".join(cleaned).strip() or None
    return None


def _parse_java_file(
    path: Path,
    repo_root: Path,
    source: str,
    token_limit: int = 512,
) -> list[dict[str, Any]]:
    """Parse a Java file using tree-sitter and extract nodes."""
    rel_path = path.relative_to(repo_root).as_posix()
    source_lines = source.splitlines()
    lang = _get_java_language()
    parser = TSParser(lang)
    tree = parser.parse(bytes(source, "utf8"))
    root = tree.root_node

    nodes: list[dict[str, Any]] = []

    file_hash = _sha256(source)
    nodes.append({
        "id": f"{rel_path}::file::{rel_path}",
        "file_path": rel_path,
        "node_type": "file",
        "name": path.name,
        "qualified_name": rel_path,
        "signature": None,
        "docstring": None,
        "start_line": 1,
        "end_line": len(source_lines),
        "language": "java",
        "raw_source": source,
        "content_hash": file_hash,
    })

    def _extract_java_nodes(parent_node, class_name: str | None = None):
        for child in parent_node.children:
            if child.type in ("class_declaration", "interface_declaration", "enum_declaration"):
                name = _ts_get_name(child)
                if not name:
                    continue
                if child.type == "interface_declaration":
                    node_type = "interface"
                elif child.type == "enum_declaration":
                    node_type = "class"
                else:
                    node_type = "class"
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                raw = _get_source_segment(source_lines, start_line, end_line)
                docstring = _java_get_docstring(child, source)
                qname = f"{class_name}.{name}" if class_name else name
                nodes.append({
                    "id": f"{rel_path}::{node_type}::{qname}",
                    "file_path": rel_path,
                    "node_type": node_type,
                    "name": name,
                    "qualified_name": qname,
                    "signature": None,
                    "docstring": docstring,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": "java",
                    "raw_source": raw,
                    "content_hash": _sha256(raw),
                })
                body = child.child_by_field_name("body")
                if body:
                    _extract_java_nodes(body, name)

            elif child.type in ("method_declaration", "constructor_declaration"):
                name = _ts_get_name(child)
                if not name:
                    continue
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                raw = _get_source_segment(source_lines, start_line, end_line)
                sig = _java_get_signature(child, source)
                docstring = _java_get_docstring(child, source)
                if class_name:
                    node_type = "method"
                    qname = f"{class_name}.{name}"
                else:
                    node_type = "function"
                    qname = name
                node_dict = {
                    "id": f"{rel_path}::{node_type}::{qname}",
                    "file_path": rel_path,
                    "node_type": node_type,
                    "name": name,
                    "qualified_name": qname,
                    "signature": sig,
                    "docstring": docstring,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": "java",
                    "raw_source": raw,
                    "content_hash": _sha256(raw),
                }
                if _estimate_tokens(raw) > token_limit:
                    nodes.append(node_dict)
                    _chunk_treesitter_node(node_dict, source_lines, token_limit, child, nodes)
                else:
                    nodes.append(node_dict)

    _extract_java_nodes(root)
    return nodes


def _ruby_get_signature(node, source: str) -> str | None:
    """Extract method signature from a Ruby method or singleton_method node."""
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return None
    return source[params_node.start_byte : params_node.end_byte]


def _ruby_get_docstring(node, source: str) -> str | None:
    """Extract preceding comment block as docstring (RDoc/YARD style).

    First tries tree-sitter prev_named_sibling. Falls back to scanning
    raw source lines above the node for consecutive # comments.
    """
    comments: list[str] = []
    prev = node.prev_named_sibling
    while prev and prev.type == "comment":
        comments.insert(0, source[prev.start_byte : prev.end_byte])
        prev = prev.prev_named_sibling

    if not comments:
        source_lines = source.splitlines()
        line_idx = node.start_point[0] - 1
        while line_idx >= 0:
            stripped = source_lines[line_idx].strip()
            if stripped.startswith("#"):
                comments.insert(0, stripped)
                line_idx -= 1
            elif stripped == "":
                break
            else:
                break

    if not comments:
        return None
    cleaned = []
    for line in comments:
        line = line.lstrip("#").strip()
        cleaned.append(line)
    return "\n".join(cleaned).strip() or None


def _parse_ruby_file(
    path: Path,
    repo_root: Path,
    source: str,
    token_limit: int = 512,
) -> list[dict[str, Any]]:
    """Parse a Ruby file using tree-sitter and extract nodes."""
    rel_path = path.relative_to(repo_root).as_posix()
    source_lines = source.splitlines()
    lang = _get_ruby_language()
    parser = TSParser(lang)
    tree = parser.parse(bytes(source, "utf8"))
    root = tree.root_node

    nodes: list[dict[str, Any]] = []

    file_hash = _sha256(source)
    nodes.append({
        "id": f"{rel_path}::file::{rel_path}",
        "file_path": rel_path,
        "node_type": "file",
        "name": path.name,
        "qualified_name": rel_path,
        "signature": None,
        "docstring": None,
        "start_line": 1,
        "end_line": len(source_lines),
        "language": "ruby",
        "raw_source": source,
        "content_hash": file_hash,
    })

    def _extract_ruby_nodes(parent_node, class_name: str | None = None):
        for child in parent_node.children:
            if child.type == "class":
                name_node = child.child_by_field_name("name")
                if not name_node:
                    continue
                name = name_node.text.decode("utf8")
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                raw = _get_source_segment(source_lines, start_line, end_line)
                docstring = _ruby_get_docstring(child, source)
                qname = f"{class_name}.{name}" if class_name else name
                nodes.append({
                    "id": f"{rel_path}::class::{qname}",
                    "file_path": rel_path,
                    "node_type": "class",
                    "name": name,
                    "qualified_name": qname,
                    "signature": None,
                    "docstring": docstring,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": "ruby",
                    "raw_source": raw,
                    "content_hash": _sha256(raw),
                })
                body = child.child_by_field_name("body")
                if body:
                    _extract_ruby_nodes(body, name)

            elif child.type == "module":
                name_node = child.child_by_field_name("name")
                if not name_node:
                    continue
                name = name_node.text.decode("utf8")
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                raw = _get_source_segment(source_lines, start_line, end_line)
                docstring = _ruby_get_docstring(child, source)
                qname = f"{class_name}.{name}" if class_name else name
                nodes.append({
                    "id": f"{rel_path}::class::{qname}",
                    "file_path": rel_path,
                    "node_type": "class",
                    "name": name,
                    "qualified_name": qname,
                    "signature": None,
                    "docstring": docstring,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": "ruby",
                    "raw_source": raw,
                    "content_hash": _sha256(raw),
                })
                body = child.child_by_field_name("body")
                if body:
                    _extract_ruby_nodes(body, name)

            elif child.type == "method":
                name_node = child.child_by_field_name("name")
                if not name_node:
                    continue
                name = name_node.text.decode("utf8")
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                raw = _get_source_segment(source_lines, start_line, end_line)
                sig = _ruby_get_signature(child, source)
                docstring = _ruby_get_docstring(child, source)
                if class_name:
                    node_type = "method"
                    qname = f"{class_name}.{name}"
                else:
                    node_type = "function"
                    qname = name
                node_dict = {
                    "id": f"{rel_path}::{node_type}::{qname}",
                    "file_path": rel_path,
                    "node_type": node_type,
                    "name": name,
                    "qualified_name": qname,
                    "signature": sig,
                    "docstring": docstring,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": "ruby",
                    "raw_source": raw,
                    "content_hash": _sha256(raw),
                }
                if _estimate_tokens(raw) > token_limit:
                    nodes.append(node_dict)
                    _chunk_treesitter_node(node_dict, source_lines, token_limit, child, nodes)
                else:
                    nodes.append(node_dict)

            elif child.type == "singleton_method":
                name_node = child.child_by_field_name("name")
                if not name_node:
                    continue
                name = name_node.text.decode("utf8")
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                raw = _get_source_segment(source_lines, start_line, end_line)
                sig = _ruby_get_signature(child, source)
                docstring = _ruby_get_docstring(child, source)
                if class_name:
                    node_type = "method"
                    qname = f"{class_name}.{name}"
                else:
                    node_type = "function"
                    qname = name
                node_dict = {
                    "id": f"{rel_path}::{node_type}::{qname}",
                    "file_path": rel_path,
                    "node_type": node_type,
                    "name": name,
                    "qualified_name": qname,
                    "signature": sig,
                    "docstring": docstring,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": "ruby",
                    "raw_source": raw,
                    "content_hash": _sha256(raw),
                }
                if _estimate_tokens(raw) > token_limit:
                    nodes.append(node_dict)
                    _chunk_treesitter_node(node_dict, source_lines, token_limit, child, nodes)
                else:
                    nodes.append(node_dict)

    _extract_ruby_nodes(root)
    return nodes


def _extract_ts_methods_from_interface(
    body_node, interface_name: str, rel_path: str, source: str,
    source_lines: list[str], token_limit: int, nodes: list[dict[str, Any]],
):
    """Extract method signatures from a TypeScript interface body."""
    for child in body_node.children:
        if child.type == "method_signature":
            name = _ts_get_name(child)
            if not name:
                continue
            start_line = child.start_point[0] + 1
            end_line = child.end_point[0] + 1
            raw = _get_source_segment(source_lines, start_line, end_line)
            sig = _ts_get_signature_typescript(child, source)
            qname = f"{interface_name}.{name}"
            nodes.append({
                "id": f"{rel_path}::method::{qname}",
                "file_path": rel_path,
                "node_type": "method",
                "name": name,
                "qualified_name": qname,
                "signature": sig,
                "docstring": None,
                "start_line": start_line,
                "end_line": end_line,
                "language": "typescript",
                "raw_source": raw,
                "content_hash": _sha256(raw),
            })


def _chunk_treesitter_node(
    node_dict: dict[str, Any],
    source_lines: list[str],
    token_limit: int,
    ts_node,
    nodes_list: list[dict[str, Any]],
):
    """Apply cAST chunking to a tree-sitter node that exceeds token limit.

    Splits the function body into chunks that fit within the token limit.
    Appends chunk nodes to nodes_list (parent already in list).
    """
    # Find the body/block child
    body_node = None
    for child in ts_node.children:
        if child.type in ("function_body", "statement_block", "block"):
            body_node = child
            break
    if body_node is None:
        return

    parent_qname = node_dict["qualified_name"]
    parent_file_path = node_dict["file_path"]
    parent_node_type = node_dict["node_type"]

    # Get top-level statements in the body
    stmts = [c for c in body_node.children if c.is_named]
    if not stmts:
        return

    current_stmts = []
    current_start = None
    chunk_idx = 0

    for stmt in stmts:
        stmt_start = stmt.start_point[0] + 1
        stmt_end = stmt.end_point[0] + 1

        if current_stmts:
            combined_source = _get_source_segment(source_lines, current_start, stmt_end)
            if _estimate_tokens(combined_source) > token_limit:
                # Flush current chunk
                last_end = current_stmts[-1].end_point[0] + 1
                chunk_source = _get_source_segment(source_lines, current_start, last_end)
                chunk_idx += 1
                chunk_qname = f"{parent_qname}.chunk_{chunk_idx}"
                chunk_id = f"{parent_file_path}::{parent_node_type}::{chunk_qname}"
                nodes_list.append({
                    "id": chunk_id,
                    "file_path": parent_file_path,
                    "node_type": parent_node_type,
                    "name": f"chunk_{chunk_idx}",
                    "qualified_name": chunk_qname,
                    "signature": node_dict.get("signature"),
                    "docstring": None,
                    "start_line": current_start,
                    "end_line": last_end,
                    "language": node_dict["language"],
                    "raw_source": chunk_source,
                    "content_hash": _sha256(chunk_source),
                })
                current_stmts = [stmt]
                current_start = stmt_start
            else:
                current_stmts.append(stmt)
        else:
            current_stmts = [stmt]
            current_start = stmt_start

    # Flush remaining
    if current_stmts:
        last_end = current_stmts[-1].end_point[0] + 1
        chunk_source = _get_source_segment(source_lines, current_start, last_end)
        chunk_idx += 1
        chunk_qname = f"{parent_qname}.chunk_{chunk_idx}"
        chunk_id = f"{parent_file_path}::{parent_node_type}::{chunk_qname}"
        nodes_list.append({
            "id": chunk_id,
            "file_path": parent_file_path,
            "node_type": parent_node_type,
            "name": f"chunk_{chunk_idx}",
            "qualified_name": chunk_qname,
            "signature": node_dict.get("signature"),
            "docstring": None,
            "start_line": current_start,
            "end_line": last_end,
            "language": node_dict["language"],
            "raw_source": chunk_source,
            "content_hash": _sha256(chunk_source),
        })


def chunk_node(
    node_dict: dict[str, Any],
    source_lines: list[str],
    token_limit: int,
    ast_node: ast.AST,
) -> list[dict[str, Any]]:
    """Split an oversized function/method into syntactically complete subtrees.

    Returns a list of chunk node dicts with parent-child qualified_name hierarchy.
    The original node dict is also returned (as the parent).
    """
    raw_source = node_dict["raw_source"]
    if _estimate_tokens(raw_source) <= token_limit:
        return [node_dict]

    # Split by top-level statements in the function body
    if not isinstance(ast_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [node_dict]

    body = ast_node.body
    if not body:
        return [node_dict]

    parent_qname = node_dict["qualified_name"]
    parent_file_path = node_dict["file_path"]
    parent_node_type = node_dict["node_type"]
    chunks = [node_dict]  # parent first

    # Group body statements into chunks that fit within token limit
    current_stmts: list[ast.AST] = []
    current_start = None
    chunk_idx = 0

    for stmt in body:
        stmt_start = stmt.lineno
        stmt_end = stmt.end_lineno or stmt.lineno
        stmt_source = _get_source_segment(source_lines, stmt_start, stmt_end)

        if current_stmts:
            # Check if adding this statement exceeds the limit
            combined_start = current_start
            combined_end = stmt_end
            combined_source = _get_source_segment(source_lines, combined_start, combined_end)
            if _estimate_tokens(combined_source) > token_limit:
                # Flush current chunk
                chunk_source = _get_source_segment(source_lines, current_start, current_stmts[-1].end_lineno or current_stmts[-1].lineno)
                chunk_idx += 1
                chunk_qname = f"{parent_qname}.chunk_{chunk_idx}"
                chunk_id = f"{parent_file_path}::{parent_node_type}::{chunk_qname}"
                chunks.append({
                    "id": chunk_id,
                    "file_path": parent_file_path,
                    "node_type": parent_node_type,
                    "name": f"chunk_{chunk_idx}",
                    "qualified_name": chunk_qname,
                    "signature": node_dict.get("signature"),
                    "docstring": None,
                    "start_line": current_start,
                    "end_line": current_stmts[-1].end_lineno or current_stmts[-1].lineno,
                    "language": node_dict["language"],
                    "raw_source": chunk_source,
                    "content_hash": _sha256(chunk_source),
                })
                current_stmts = [stmt]
                current_start = stmt_start
            else:
                current_stmts.append(stmt)
        else:
            current_stmts = [stmt]
            current_start = stmt_start

    # Flush remaining
    if current_stmts:
        chunk_source = _get_source_segment(
            source_lines, current_start,
            current_stmts[-1].end_lineno or current_stmts[-1].lineno,
        )
        chunk_idx += 1
        chunk_qname = f"{parent_qname}.chunk_{chunk_idx}"
        chunk_id = f"{parent_file_path}::{parent_node_type}::{chunk_qname}"
        chunks.append({
            "id": chunk_id,
            "file_path": parent_file_path,
            "node_type": parent_node_type,
            "name": f"chunk_{chunk_idx}",
            "qualified_name": chunk_qname,
            "signature": node_dict.get("signature"),
            "docstring": None,
            "start_line": current_start,
            "end_line": current_stmts[-1].end_lineno or current_stmts[-1].lineno,
            "language": node_dict["language"],
            "raw_source": chunk_source,
            "content_hash": _sha256(chunk_source),
        })

    return chunks


def parse_file(
    path: Path,
    conn: Any,
    repo_root: Path,
    token_limit: int = 512,
) -> list[dict[str, Any]]:
    """Parse a source file and extract nodes.

    Dispatches to the appropriate parser based on file extension:
    - .py → Python ast stdlib
    - .kt → tree-sitter-kotlin
    - .ts/.tsx → tree-sitter-typescript

    Returns list of node dicts with all required fields.
    """
    path = Path(path)
    repo_root = Path(repo_root)

    language = detect_language(path)
    if language is None:
        return []

    source = path.read_text(encoding="utf-8")

    if language == "kotlin":
        return _parse_kotlin_file(path, repo_root, source, token_limit)
    elif language == "typescript":
        return _parse_typescript_file(path, repo_root, source, token_limit)
    elif language == "java":
        return _parse_java_file(path, repo_root, source, token_limit)
    elif language == "ruby":
        return _parse_ruby_file(path, repo_root, source, token_limit)

    # Python path (original)
    rel_path = path.relative_to(repo_root).as_posix()
    source_lines = source.splitlines()

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        click.echo(f"[WARNING] Skipped: {rel_path} — {e}", err=True)
        return []

    nodes: list[dict[str, Any]] = []

    # File node
    file_hash = _sha256(source)
    file_node = {
        "id": f"{rel_path}::file::{rel_path}",
        "file_path": rel_path,
        "node_type": "file",
        "name": path.name,
        "qualified_name": rel_path,
        "signature": None,
        "docstring": ast.get_docstring(tree),
        "start_line": 1,
        "end_line": len(source_lines),
        "language": "python",
        "raw_source": source,
        "content_hash": file_hash,
    }
    nodes.append(file_node)

    # Walk top-level and class-level definitions
    for top_node in ast.iter_child_nodes(tree):
        if isinstance(top_node, ast.ClassDef):
            class_start = top_node.lineno
            class_end = top_node.end_lineno or top_node.lineno
            class_source = _get_source_segment(source_lines, class_start, class_end)
            class_dict = {
                "id": f"{rel_path}::class::{top_node.name}",
                "file_path": rel_path,
                "node_type": "class",
                "name": top_node.name,
                "qualified_name": top_node.name,
                "signature": None,
                "docstring": _get_docstring(top_node),
                "start_line": class_start,
                "end_line": class_end,
                "language": "python",
                "raw_source": class_source,
                "content_hash": _sha256(class_source),
            }
            nodes.append(class_dict)

            # Methods
            for item in ast.iter_child_nodes(top_node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_start = item.lineno
                    method_end = item.end_lineno or item.lineno
                    method_source = _get_source_segment(source_lines, method_start, method_end)
                    qname = f"{top_node.name}.{item.name}"
                    method_dict = {
                        "id": f"{rel_path}::method::{qname}",
                        "file_path": rel_path,
                        "node_type": "method",
                        "name": item.name,
                        "qualified_name": qname,
                        "signature": _get_signature(item),
                        "docstring": _get_docstring(item),
                        "start_line": method_start,
                        "end_line": method_end,
                        "language": "python",
                        "raw_source": method_source,
                        "content_hash": _sha256(method_source),
                    }
                    expanded = chunk_node(method_dict, source_lines, token_limit, item)
                    nodes.extend(expanded)

        elif isinstance(top_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_start = top_node.lineno
            func_end = top_node.end_lineno or top_node.lineno
            func_source = _get_source_segment(source_lines, func_start, func_end)
            func_dict = {
                "id": f"{rel_path}::function::{top_node.name}",
                "file_path": rel_path,
                "node_type": "function",
                "name": top_node.name,
                "qualified_name": top_node.name,
                "signature": _get_signature(top_node),
                "docstring": _get_docstring(top_node),
                "start_line": func_start,
                "end_line": func_end,
                "language": "python",
                "raw_source": func_source,
                "content_hash": _sha256(func_source),
            }
            expanded = chunk_node(func_dict, source_lines, token_limit, top_node)
            nodes.extend(expanded)

    return nodes


def parse_directory(
    repo_root: Path,
    conn: Any,
    token_limit: int = 512,
    exclude_patterns: list[str] | None = None,
) -> list[str]:
    """Walk directory, detect language, parse .py files. Returns list of warnings.

    Implements incremental detection: skips files whose content_hash hasn't changed.
    When upserting nodes with changed content_hash, clears enriched_at to NULL.
    """
    repo_root = Path(repo_root)
    warnings: list[str] = []
    gitignore_patterns = _load_gitignore_patterns(repo_root)
    if exclude_patterns:
        gitignore_patterns.extend(exclude_patterns)

    # Collect candidate files first for progress reporting
    candidate_files: list[tuple[Path, str]] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d != "__pycache__"
        ]
        for filename in filenames:
            file_path = Path(dirpath) / filename
            language = detect_language(file_path)
            if language is None:
                continue
            if _is_ignored(file_path, repo_root, gitignore_patterns):
                continue
            candidate_files.append((file_path, language))

    total_files = len(candidate_files)
    processed = 0
    skipped_unchanged = 0

    for file_path, language in candidate_files:
        rel_path = file_path.relative_to(repo_root).as_posix()

        # Incremental detection: check file content hash
        file_content = file_path.read_bytes()
        file_hash = hashlib.sha256(file_content).hexdigest()

        existing = conn.execute(
            "SELECT content_hash FROM files WHERE path = ?",
            (rel_path,),
        ).fetchone()

        if existing and existing[0] == file_hash:
            skipped_unchanged += 1
            continue

        processed += 1

        # Parse the file
        nodes = parse_file(file_path, conn, repo_root, token_limit=token_limit)

        if not nodes:
            warnings.append(f"Skipped: {rel_path}")
            continue

        # Delete old nodes for this file
        conn.execute("DELETE FROM nodes WHERE file_path = ?", (rel_path,))

        # Insert new nodes; clear enriched_at to NULL
        for node in nodes:
            conn.execute(
                """INSERT OR REPLACE INTO nodes
                (id, file_path, node_type, name, qualified_name, signature,
                 docstring, start_line, end_line, language, raw_source,
                 content_hash, enriched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                (
                    node["id"],
                    node["file_path"],
                    node["node_type"],
                    node["name"],
                    node["qualified_name"],
                    node.get("signature"),
                    node.get("docstring"),
                    node["start_line"],
                    node["end_line"],
                    node["language"],
                    node["raw_source"],
                    node["content_hash"],
                ),
            )

        # Upsert files table
        now = datetime.now(timezone.utc).isoformat()
        last_modified = datetime.fromtimestamp(
            file_path.stat().st_mtime, tz=timezone.utc
        ).isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO files
            (path, last_modified, content_hash, language, node_count, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (rel_path, last_modified, file_hash, language, len(nodes), now),
        )

        conn.commit()

    if skipped_unchanged:
        click.echo(f"[PHASE 1] Skipped {skipped_unchanged} unchanged files", err=True)

    return warnings
