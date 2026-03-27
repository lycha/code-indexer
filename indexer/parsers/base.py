"""Phase 1: AST parsing, incremental detection, and cAST chunking.

Shared utilities, dispatch logic, and the parse_directory orchestrator.
Per-language parsers live in sibling modules.
"""

import hashlib
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
import pathspec

__all__ = ["parse_file", "parse_directory"]


# ---------------------------------------------------------------------------
# Shared utility functions
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    """Estimate token count: ~4 characters per token."""
    return max(1, len(text) // 4)


def _sha256(text: str) -> str:
    """Return SHA-256 hex digest of text."""
    return hashlib.sha256(text.encode()).hexdigest()


def _get_source_segment(source_lines: list[str], start_line: int, end_line: int) -> str:
    """Extract source lines (1-based inclusive)."""
    return "\n".join(source_lines[start_line - 1 : end_line])


def _load_gitignore_patterns(
    repo_root: Path,
    extra_patterns: list[str] | None = None,
) -> pathspec.PathSpec:
    """Load .gitignore patterns from repo root and return a PathSpec matcher."""
    lines: list[str] = []
    gitignore = repo_root / ".gitignore"
    if gitignore.exists():
        for raw_line in gitignore.read_text().splitlines():
            stripped = raw_line.strip()
            if stripped and not stripped.startswith("#"):
                lines.append(stripped)
    if extra_patterns:
        lines.extend(extra_patterns)
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def _is_ignored(file_path: Path, repo_root: Path, spec: pathspec.PathSpec) -> bool:
    """Check if a file should be ignored based on .gitignore patterns and built-in rules."""
    rel = file_path.relative_to(repo_root)

    # Always exclude .codeindex/*.db
    if ".codeindex" in rel.parts:
        return True

    return spec.match_file(rel.as_posix())


def _file_content_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of file contents."""
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


_MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB


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
# tree-sitter shared utilities
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
        if child.type in ("identifier", "type_identifier", "property_identifier", "simple_identifier"):
            return child.text.decode("utf8")
    return None


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


# ---------------------------------------------------------------------------
# cAST chunking (tree-sitter)
# ---------------------------------------------------------------------------


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
        if child.type in ("function_body", "statement_block", "block", "body_statement"):
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


# ---------------------------------------------------------------------------
# cAST merge step
# ---------------------------------------------------------------------------

# Node types that are never merged (containers / structural nodes).
_NON_MERGEABLE_TYPES = frozenset({"file", "class", "interface", "object"})


def _merge_small_nodes(nodes: list[dict[str, Any]], token_limit: int) -> list[dict[str, Any]]:
    """Merge consecutive small sibling nodes within the same parent.

    After cAST chunking splits large nodes, many small sibling functions /
    methods remain as individual nodes.  This step merges consecutive small
    siblings whose combined token count fits within *token_limit*, improving
    retrieval efficiency.

    Rules:
    - Only function / method nodes are candidates (not files, classes, etc.).
    - Chunk nodes (name starts with ``chunk_``) are never merged.
    - Nodes are grouped by their parent qualified name (derived from the
      ``qualified_name`` field: everything before the last ``.``).
    - Within each group the original list order is preserved.
    - A node is "small" when ``_estimate_tokens(raw_source) < token_limit / 3``.
    - Consecutive small siblings are accumulated until adding the next would
      exceed *token_limit*; the accumulator is then flushed as a single merged
      node.
    - A single remaining small node (nothing to merge with) is left as-is.
    """
    if not nodes:
        return nodes

    small_threshold = token_limit / 3

    # We need to preserve the overall order of nodes while only potentially
    # merging eligible ones.  Strategy: walk the node list, keep
    # non-mergeable nodes in place, and for runs of mergeable nodes sharing
    # the same parent, apply the merge logic.

    result: list[dict[str, Any]] = []
    # Buffer of consecutive mergeable nodes with the same parent
    merge_buffer: list[dict[str, Any]] = []
    current_parent: str | None = None
    merge_counter = 0  # global counter across the whole file for unique naming

    def _parent_qname(node: dict[str, Any]) -> str:
        """Derive the parent qualified name from a node's qualified_name."""
        qn = node["qualified_name"]
        if "." in qn:
            return qn.rsplit(".", 1)[0]
        # Top-level function → parent is the file
        return node["file_path"]

    def _is_mergeable(node: dict[str, Any]) -> bool:
        """Check if a node is a candidate for merging."""
        if node["node_type"] in _NON_MERGEABLE_TYPES:
            return False
        # Chunk nodes are never merged
        if node["name"].startswith("chunk_"):
            return False
        return True

    def _flush_buffer():
        """Merge accumulated buffer nodes and append results to *result*."""
        nonlocal merge_buffer, merge_counter
        if not merge_buffer:
            return

        # Identify small vs large within the buffer
        groups: list[list[dict[str, Any]]] = []  # runs of consecutive small nodes
        temp_small: list[dict[str, Any]] = []

        for node in merge_buffer:
            is_small = _estimate_tokens(node["raw_source"]) < small_threshold
            if is_small:
                temp_small.append(node)
            else:
                if temp_small:
                    groups.append(temp_small)
                    temp_small = []
                groups.append([node])  # large node stands alone

        if temp_small:
            groups.append(temp_small)

        for group in groups:
            if len(group) == 1:
                # Single node (small or large) → pass through
                result.append(group[0])
                continue

            # Multiple consecutive small nodes → merge them in batches
            accumulator: list[dict[str, Any]] = []
            acc_tokens = 0

            for node in group:
                node_tokens = _estimate_tokens(node["raw_source"])
                if accumulator and acc_tokens + node_tokens > token_limit:
                    # Flush accumulator as a merged node
                    _emit_merged(accumulator)
                    accumulator = [node]
                    acc_tokens = node_tokens
                else:
                    accumulator.append(node)
                    acc_tokens += node_tokens

            # Flush remaining accumulator
            if len(accumulator) == 1:
                result.append(accumulator[0])
            elif accumulator:
                _emit_merged(accumulator)

        merge_buffer = []

    def _emit_merged(acc: list[dict[str, Any]]):
        """Keep original nodes and add a merged retrieval node from *acc*."""
        nonlocal merge_counter
        merge_counter += 1

        # Keep all original nodes (preserves individual identity)
        result.extend(acc)

        # Add a combined merged node for retrieval optimisation
        combined_source = "\n\n".join(n["raw_source"] for n in acc)
        parent_qn = _parent_qname(acc[0])
        merged_qname = f"{parent_qn}.merged_{merge_counter}"
        first = acc[0]

        # Build a combined signature from constituents
        sigs = [n.get("signature") for n in acc if n.get("signature")]
        combined_sig = "; ".join(sigs) if sigs else None

        result.append({
            "id": f"{first['file_path']}::{first['node_type']}::{merged_qname}",
            "file_path": first["file_path"],
            "node_type": first["node_type"],
            "name": f"merged_{merge_counter}",
            "qualified_name": merged_qname,
            "signature": combined_sig,
            "docstring": None,
            "start_line": min(n["start_line"] for n in acc),
            "end_line": max(n["end_line"] for n in acc),
            "language": first["language"],
            "raw_source": combined_source,
            "content_hash": _sha256(combined_source),
        })

    # Main loop
    for node in nodes:
        if not _is_mergeable(node):
            _flush_buffer()
            current_parent = None
            result.append(node)
            continue

        parent = _parent_qname(node)
        if parent != current_parent:
            _flush_buffer()
            current_parent = parent

        merge_buffer.append(node)

    _flush_buffer()
    return result


# ---------------------------------------------------------------------------
# parse_file dispatcher
# ---------------------------------------------------------------------------


def parse_file(
    path: Path,
    conn: sqlite3.Connection,
    repo_root: Path,
    token_limit: int = 512,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Parse a source file and extract nodes.

    Dispatches to the appropriate parser based on file extension:
    - .py → Python ast stdlib
    - .kt → tree-sitter-kotlin
    - .ts/.tsx → tree-sitter-typescript
    - .java → tree-sitter-java
    - .rb → tree-sitter-ruby

    Returns list of node dicts with all required fields.
    """
    # Lazy imports to avoid circular dependencies at module load time
    from indexer.parsers.kotlin_parser import _parse_kotlin_file
    from indexer.parsers.typescript_parser import _parse_typescript_file
    from indexer.parsers.java_parser import _parse_java_file
    from indexer.parsers.ruby_parser import _parse_ruby_file
    from indexer.parsers.python_parser import _parse_python_file

    path = Path(path)
    repo_root = Path(repo_root)

    language = detect_language(path)
    if language is None:
        return []

    if source is None:
        try:
            file_size = path.stat().st_size
        except OSError:
            return []
        if file_size > _MAX_FILE_SIZE:
            click.echo(f"[WARNING] Skipping oversized file ({file_size // 1024}KB): {path}", err=True)
            return []
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            click.echo(f"[WARNING] Skipping non-UTF-8 file: {path}", err=True)
            return []

    source_lines = source.splitlines()

    if language == "kotlin":
        nodes = _parse_kotlin_file(path, repo_root, source, token_limit)
    elif language == "typescript":
        nodes = _parse_typescript_file(path, repo_root, source, token_limit)
    elif language == "java":
        nodes = _parse_java_file(path, repo_root, source, token_limit)
    elif language == "ruby":
        nodes = _parse_ruby_file(path, repo_root, source, token_limit)
    else:
        # Python
        nodes = _parse_python_file(path, repo_root, source, source_lines, token_limit)

    # Apply cAST merge step: merge consecutive small sibling nodes
    nodes = _merge_small_nodes(nodes, token_limit)

    return nodes


# ---------------------------------------------------------------------------
# parse_directory orchestrator
# ---------------------------------------------------------------------------


def parse_directory(
    repo_root: Path,
    conn: sqlite3.Connection,
    token_limit: int = 512,
    exclude_patterns: list[str] | None = None,
) -> tuple[list[str], set[str]]:
    """Walk directory, detect language, parse .py files.

    Returns a tuple of (warnings, changed_files) where:
    - warnings: list of warning messages for files that could not be parsed
    - changed_files: set of relative file paths that were re-parsed or deleted

    Implements incremental detection: skips files whose content_hash hasn't changed.
    When upserting nodes with changed content_hash, clears enriched_at to NULL.
    """
    repo_root = Path(repo_root)
    warnings: list[str] = []
    changed_files: set[str] = set()
    gitignore_spec = _load_gitignore_patterns(repo_root, extra_patterns=exclude_patterns)

    # Collect candidate files first for progress reporting
    candidate_files: list[tuple[Path, str]] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d != "__pycache__"
            and not (Path(dirpath) / d).is_symlink()
        ]
        for filename in filenames:
            file_path = Path(dirpath) / filename
            if file_path.is_symlink():
                continue
            language = detect_language(file_path)
            if language is None:
                continue
            if _is_ignored(file_path, repo_root, gitignore_spec):
                continue
            candidate_files.append((file_path, language))

    total_files = len(candidate_files)
    processed = 0
    skipped_unchanged = 0
    batch_count = 0

    for file_path, language in candidate_files:
        rel_path = file_path.relative_to(repo_root).as_posix()

        # File size guard: skip oversized files to prevent OOM
        try:
            file_size = file_path.stat().st_size
        except OSError:
            continue
        if file_size > _MAX_FILE_SIZE:
            click.echo(f"[WARNING] Skipping oversized file ({file_size // 1024}KB): {rel_path}", err=True)
            warnings.append(f"Skipped oversized file: {rel_path}")
            continue

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

        # Decode once for reuse in parse_file (avoid double read)
        try:
            source = file_content.decode("utf-8")
        except UnicodeDecodeError:
            click.echo(f"[WARNING] Skipping non-UTF-8 file: {file_path}", err=True)
            continue

        processed += 1
        changed_files.add(rel_path)

        # Parse the file
        nodes = parse_file(file_path, conn, repo_root, token_limit=token_limit, source=source)

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

        batch_count += 1
        if batch_count % 50 == 0:
            conn.commit()

    if batch_count > 0:
        conn.commit()

    # Deletion detection: remove files from DB that no longer exist on disk
    found_on_disk = {fp.relative_to(repo_root).as_posix() for fp, _ in candidate_files}
    db_paths = {r[0] for r in conn.execute("SELECT path FROM files").fetchall()}
    stale_paths = db_paths - found_on_disk
    for stale in stale_paths:
        conn.execute("DELETE FROM nodes WHERE file_path = ?", (stale,))
        conn.execute("DELETE FROM files WHERE path = ?", (stale,))
        click.echo(f"[PHASE 1] Removed deleted file: {stale}", err=True)
        changed_files.add(stale)
    if stale_paths:
        conn.commit()

    if skipped_unchanged:
        click.echo(f"[PHASE 1] Skipped {skipped_unchanged} unchanged files", err=True)

    return warnings, changed_files
