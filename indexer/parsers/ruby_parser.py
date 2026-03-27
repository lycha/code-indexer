"""Ruby-specific parsing via tree-sitter."""

from pathlib import Path
from typing import Any

import tree_sitter_ruby as tsruby
from tree_sitter import Language, Parser as TSParser

from indexer.parsers.base import (
    _chunk_treesitter_node,
    _estimate_tokens,
    _get_source_segment,
    _sha256,
    _ts_get_docstring,
)

# ---------------------------------------------------------------------------
# Lazy singleton
# ---------------------------------------------------------------------------

_RUBY_LANGUAGE: Language | None = None


def _get_ruby_language() -> Language:
    global _RUBY_LANGUAGE
    if _RUBY_LANGUAGE is None:
        _RUBY_LANGUAGE = Language(tsruby.language())
    return _RUBY_LANGUAGE


# ---------------------------------------------------------------------------
# Ruby-specific helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


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
