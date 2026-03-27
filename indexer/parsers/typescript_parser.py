"""TypeScript-specific parsing via tree-sitter."""

from pathlib import Path
from typing import Any

import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Parser as TSParser

from indexer.parsers.base import (
    _chunk_treesitter_node,
    _estimate_tokens,
    _get_source_segment,
    _sha256,
    _ts_get_docstring,
    _ts_get_name,
)

# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

_TS_LANGUAGE: Language | None = None
_TSX_LANGUAGE: Language | None = None


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


# ---------------------------------------------------------------------------
# TypeScript-specific helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


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
