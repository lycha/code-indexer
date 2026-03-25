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
    """Parse a Python file using ast stdlib and extract nodes.

    Returns list of node dicts with all required fields.
    """
    path = Path(path)
    repo_root = Path(repo_root)
    rel_path = path.relative_to(repo_root).as_posix()

    source = path.read_text(encoding="utf-8")
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
) -> list[str]:
    """Walk directory, detect language, parse .py files. Returns list of warnings.

    Implements incremental detection: skips files whose content_hash hasn't changed.
    When upserting nodes with changed content_hash, clears enriched_at to NULL.
    """
    repo_root = Path(repo_root)
    warnings: list[str] = []
    gitignore_patterns = _load_gitignore_patterns(repo_root)

    for dirpath, dirnames, filenames in os.walk(repo_root):
        # Skip hidden directories and __pycache__
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d != "__pycache__"
        ]

        for filename in filenames:
            if not filename.endswith(".py"):
                continue

            file_path = Path(dirpath) / filename

            if _is_ignored(file_path, repo_root, gitignore_patterns):
                continue

            rel_path = file_path.relative_to(repo_root).as_posix()

            # Incremental detection: check file content hash
            file_content = file_path.read_bytes()
            file_hash = hashlib.sha256(file_content).hexdigest()

            existing = conn.execute(
                "SELECT content_hash FROM files WHERE path = ?",
                (rel_path,),
            ).fetchone()

            if existing and existing[0] == file_hash:
                # File unchanged, skip
                continue

            # Parse the file
            nodes = parse_file(file_path, conn, repo_root, token_limit=token_limit)

            if not nodes:
                # Syntax error or empty file - parse_file already logged warning
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
                (rel_path, last_modified, file_hash, "python", len(nodes), now),
            )

            conn.commit()

    return warnings
