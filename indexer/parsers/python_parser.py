"""Python-specific parsing via the ast stdlib module."""

import ast
from pathlib import Path
from typing import Any

import click

from indexer.parsers.base import (
    _estimate_tokens,
    _get_source_segment,
    _sha256,
)

# ---------------------------------------------------------------------------
# Python AST helpers
# ---------------------------------------------------------------------------


def _get_signature(node: ast.AST) -> str | None:
    """Extract function/method signature from AST node."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        parts = []
        # Positional-only parameters (before /)
        for arg in node.args.posonlyargs:
            ann = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
            parts.append(f"{arg.arg}{ann}")
        if node.args.posonlyargs:
            parts.append("/")
        # Regular parameters
        for arg in node.args.args:
            ann = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
            parts.append(f"{arg.arg}{ann}")
        # *args or bare * separator
        if node.args.vararg:
            parts.append(f"*{node.args.vararg.arg}")
        elif node.args.kwonlyargs:
            parts.append("*")
        # Keyword-only parameters
        for arg in node.args.kwonlyargs:
            ann = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
            parts.append(f"{arg.arg}{ann}")
        # **kwargs
        if node.args.kwarg:
            parts.append(f"**{node.args.kwarg.arg}")
        returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        return f"({', '.join(parts)}){returns}"
    return None


def _get_docstring(node: ast.AST) -> str | None:
    """Extract docstring from a class or function node."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return ast.get_docstring(node)
    return None


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


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def _parse_python_file(
    path: Path,
    repo_root: Path,
    source: str,
    source_lines: list[str],
    token_limit: int = 512,
) -> list[dict[str, Any]]:
    """Parse a Python file using the ast stdlib and extract nodes."""
    rel_path = path.relative_to(repo_root).as_posix()

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
