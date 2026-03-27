"""Java-specific parsing via tree-sitter."""

from pathlib import Path
from typing import Any

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser as TSParser

from indexer.parsers.base import (
    _chunk_treesitter_node,
    _estimate_tokens,
    _get_source_segment,
    _sha256,
    _ts_get_name,
)

# ---------------------------------------------------------------------------
# Lazy singleton
# ---------------------------------------------------------------------------

_JAVA_LANGUAGE: Language | None = None


def _get_java_language() -> Language:
    global _JAVA_LANGUAGE
    if _JAVA_LANGUAGE is None:
        _JAVA_LANGUAGE = Language(tsjava.language())
    return _JAVA_LANGUAGE


# ---------------------------------------------------------------------------
# Java-specific helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


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
