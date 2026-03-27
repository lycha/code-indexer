"""Kotlin-specific parsing via tree-sitter."""

from pathlib import Path
from typing import Any

import tree_sitter_kotlin as tskotlin
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
# Lazy singleton
# ---------------------------------------------------------------------------

_KT_LANGUAGE: Language | None = None


def _get_kotlin_language() -> Language:
    global _KT_LANGUAGE
    if _KT_LANGUAGE is None:
        _KT_LANGUAGE = Language(tskotlin.language())
    return _KT_LANGUAGE


# ---------------------------------------------------------------------------
# Kotlin-specific helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


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
