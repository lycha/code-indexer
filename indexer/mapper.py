"""Phase 2: GrepRAG dependency mapping."""

import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import click

from indexer.utils import find_rg

__all__ = ["map_dependencies"]

_SQL_BATCH_SIZE = 900
_RG_BATCH_SIZE = 200


def _batched_query(conn: sqlite3.Connection, query_template: str, params: list, *, extra_params: list | None = None) -> list:
    """Execute a query with IN-clause params in batches to avoid SQLite variable limits.

    query_template must contain a single '{}' placeholder for the IN-clause.
    extra_params are appended to each batch (for non-IN-clause bindings).
    """
    results = []
    for i in range(0, len(params), _SQL_BATCH_SIZE):
        batch = params[i : i + _SQL_BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        sql = query_template.format(placeholders)
        bind = batch + (extra_params or [])
        results.extend(conn.execute(sql, bind).fetchall())
    return results


def _batched_execute(conn: sqlite3.Connection, query_template: str, params: list) -> int:
    """Execute a write statement with IN-clause params in batches.

    Returns total rowcount across batches.
    """
    total = 0
    for i in range(0, len(params), _SQL_BATCH_SIZE):
        batch = params[i : i + _SQL_BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        sql = query_template.format(placeholders)
        cursor = conn.execute(sql, batch)
        total += cursor.rowcount
    return total


def _get_changed_nodes(conn: sqlite3.Connection, changed_file_paths: list[str]) -> list[dict]:
    """Get all nodes belonging to changed files."""
    if not changed_file_paths:
        return []
    rows = _batched_query(
        conn,
        "SELECT id, file_path, node_type, name, qualified_name, start_line, end_line "
        "FROM nodes WHERE file_path IN ({})",
        changed_file_paths,
    )
    return [
        {
            "id": r[0],
            "file_path": r[1],
            "node_type": r[2],
            "name": r[3],
            "qualified_name": r[4],
            "start_line": r[5],
            "end_line": r[6],
        }
        for r in rows
    ]


def _get_exported_identifiers(node: dict) -> list[str]:
    """Get identifiers to search for from a node.

    For file nodes, skip (too broad). For others, use the short name.
    """
    if node["node_type"] == "file":
        return []
    return [node["name"]]


# ---------------------------------------------------------------------------
# In-memory node index for fast file+line -> node resolution
# ---------------------------------------------------------------------------

class _NodeIndex:
    """Pre-loaded in-memory index for resolving file+line to node IDs
    and looking up node metadata by ID, replacing per-match SQL queries.
    """

    def __init__(self, conn: sqlite3.Connection):
        rows = conn.execute(
            "SELECT id, file_path, node_type, name, qualified_name, start_line, end_line "
            "FROM nodes"
        ).fetchall()

        self._by_id: dict[str, dict] = {}
        self._by_file: dict[str, list[tuple[int, int, int, str]]] = {}

        for r in rows:
            node_id, file_path, node_type, name, qualified_name, start_line, end_line = r
            self._by_id[node_id] = {
                "id": node_id,
                "file_path": file_path,
                "node_type": node_type,
                "name": name,
                "qualified_name": qualified_name,
                "start_line": start_line,
                "end_line": end_line,
            }
            span = end_line - start_line
            self._by_file.setdefault(file_path, []).append(
                (start_line, end_line, span, node_id)
            )

        for entries in self._by_file.values():
            entries.sort(key=lambda e: e[2])

    def resolve(self, file_path: str, line: int) -> str | None:
        entries = self._by_file.get(file_path)
        if not entries:
            return None
        for start, end, _span, node_id in entries:
            if start <= line <= end:
                return node_id
        return None

    def get(self, node_id: str) -> dict | None:
        return self._by_id.get(node_id)


# ---------------------------------------------------------------------------
# Edge type classification (uses in-memory index)
# ---------------------------------------------------------------------------

def _classify_edge_type(source_node: dict, target_node_id: str, identifier: str, node_index: _NodeIndex) -> str:
    """Classify the edge type based on source/target relationship."""
    target = node_index.get(target_node_id)
    if not target:
        return "references"
    target_type = target["node_type"]
    target_name = target["name"]
    target_qname = target["qualified_name"]

    src_type = source_node["node_type"]

    if src_type == "file":
        return "imports"

    if src_type == "method" and target_type == "method":
        src_name = source_node.get("name", "")
        src_qname = source_node.get("qualified_name", "")
        if src_name == target_name and src_qname != target_qname:
            return "overrides"

    if target_type in ("class", "interface") and src_type in ("class", "interface"):
        return "inherits"

    if target_type == "class" and src_type in ("function", "method"):
        return "instantiates"

    if target_type in ("function", "method"):
        return "calls"

    return "references"


# ---------------------------------------------------------------------------
# Batched ripgrep execution
# ---------------------------------------------------------------------------

def _run_ripgrep(rg_path: str, identifier: str, repo_root: str) -> list[dict]:
    """Run ripgrep for a single identifier, return parsed JSON matches."""
    try:
        result = subprocess.run(
            [rg_path, "--json", "-n", "-w", "-F", identifier, repo_root],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        click.echo(f"[WARNING] ripgrep timed out searching for '{identifier}', skipping", err=True)
        return []
    return _parse_rg_json(result.stdout)


def _run_ripgrep_batch(rg_path: str, identifiers: list[str], repo_root: str) -> dict[str, list[dict]]:
    """Run ripgrep for multiple identifiers at once using a pattern file.

    Returns a dict mapping each identifier to its list of matches.
    """
    if not identifiers:
        return {}

    if len(identifiers) == 1:
        return {identifiers[0]: _run_ripgrep(rg_path, identifiers[0], repo_root)}

    results: dict[str, list[dict]] = {ident: [] for ident in identifiers}

    for i in range(0, len(identifiers), _RG_BATCH_SIZE):
        batch = identifiers[i : i + _RG_BATCH_SIZE]
        fd, pat_path = tempfile.mkstemp(prefix="rg_patterns_", suffix=".txt")
        try:
            with os.fdopen(fd, "w") as f:
                for ident in batch:
                    f.write(ident + "\n")
            try:
                result = subprocess.run(
                    [rg_path, "--json", "-n", "-w", "-F", "-f", pat_path, repo_root],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except subprocess.TimeoutExpired:
                click.echo(
                    f"[WARNING] ripgrep timed out on batch of {len(batch)} identifiers, skipping",
                    err=True,
                )
                continue
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("type") != "match":
                    continue
                match_data = data.get("data", {})
                path_info = match_data.get("path", {})
                file_path = path_info.get("text", "")
                line_number = match_data.get("line_number", 0)
                if not file_path or not line_number:
                    continue
                for submatch in match_data.get("submatches", []):
                    matched_text = submatch.get("match", {}).get("text", "")
                    if matched_text in results:
                        results[matched_text].append({"file": file_path, "line": line_number})
        finally:
            os.unlink(pat_path)

    return results


# ---------------------------------------------------------------------------
# Ripgrep JSON parser
# ---------------------------------------------------------------------------

def _parse_rg_json(stdout: str) -> list[dict]:
    """Parse ripgrep JSON output into match dicts."""
    matches = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("type") != "match":
            continue
        match_data = data.get("data", {})
        path_info = match_data.get("path", {})
        file_path = path_info.get("text", "")
        line_number = match_data.get("line_number", 0)
        if file_path and line_number:
            matches.append({"file": file_path, "line": line_number})
    return matches


# ---------------------------------------------------------------------------
# Edge operations
# ---------------------------------------------------------------------------

def delete_outbound_edges(conn: sqlite3.Connection, changed_node_ids: list[str]) -> int:
    """Delete outbound edges from changed nodes only.

    Returns count of deleted edges.
    """
    if not changed_node_ids:
        return 0
    return _batched_execute(
        conn,
        "DELETE FROM edges WHERE source_id IN ({})",
        changed_node_ids,
    )


def purge_dangling_edges(conn: sqlite3.Connection) -> int:
    """Remove inbound edges to deleted/renamed nodes (target no longer exists).

    Returns count of purged edges.
    """
    cursor = conn.execute(
        "DELETE FROM edges WHERE target_id NOT IN (SELECT id FROM nodes)"
    )
    return cursor.rowcount


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Unconditionally rebuild nodes_fts virtual table."""
    conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")
    conn.commit()


# ---------------------------------------------------------------------------
# Core: process matches for a set of nodes against ripgrep results
# ---------------------------------------------------------------------------

def _process_matches(
    nodes: list[dict],
    rg_path: str,
    repo_root: str,
    repo_root_path: Path,
    node_index: _NodeIndex,
    conn: sqlite3.Connection,
) -> int:
    """Run batched ripgrep for a set of nodes and insert edges.

    Returns total edges inserted.
    """
    # Build identifier -> [node, ...] mapping and deduplicate
    ident_to_nodes: dict[str, list[dict]] = {}
    for node in nodes:
        for ident in _get_exported_identifiers(node):
            ident_to_nodes.setdefault(ident, []).append(node)

    unique_identifiers = list(ident_to_nodes.keys())
    if not unique_identifiers:
        return 0

    click.echo(
        f"[PHASE 2] Searching {len(unique_identifiers)} unique identifiers "
        f"(from {len(nodes)} nodes)",
        err=True,
    )

    all_matches = _run_ripgrep_batch(rg_path, unique_identifiers, repo_root)

    edges_inserted = 0
    for ident, matches in all_matches.items():
        target_nodes = ident_to_nodes[ident]
        for match in matches:
            match_path = match["file"]
            try:
                match_p = Path(match_path)
                if match_p.is_absolute():
                    rel_path = str(match_p.relative_to(repo_root_path))
                else:
                    rel_path = match_path
            except ValueError:
                continue

            source_node_id = node_index.resolve(rel_path, match["line"])
            if source_node_id is None:
                continue

            source_info = node_index.get(source_node_id)
            if not source_info:
                continue

            for target_node in target_nodes:
                if source_node_id == target_node["id"]:
                    continue
                edge_type = _classify_edge_type(source_info, target_node["id"], ident, node_index)
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO edges (source_id, target_id, edge_type, call_site_line) "
                        "VALUES (?, ?, ?, ?)",
                        (source_node_id, target_node["id"], edge_type, match["line"]),
                    )
                    edges_inserted += 1
                except sqlite3.IntegrityError:
                    pass

    return edges_inserted


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def map_dependencies(changed_node_ids: list[str], conn: sqlite3.Connection, repo_root: str) -> int:
    """Map dependencies for changed nodes via ripgrep.

    Steps:
    1. Delete outbound edges from changed nodes
    2. Run batched ripgrep for deduplicated identifiers
    3. Resolve matches to node IDs, insert edges
    4. Re-resolve outbound edges from callers of changed nodes
    5. Purge dangling edges
    6. Rebuild FTS5

    Returns total edges inserted.
    """
    rg_path = find_rg(required=True)
    repo_root_path = Path(repo_root)

    if not changed_node_ids:
        rebuild_fts(conn)
        return 0

    # Build in-memory node index
    node_index = _NodeIndex(conn)

    # Fetch node info for changed nodes
    changed_nodes = [
        node_index.get(nid)
        for nid in changed_node_ids
        if node_index.get(nid) is not None
    ]

    # Step 0: Collect callers of changed nodes BEFORE modifying edges
    changed_set = set(changed_node_ids)
    caller_rows = _batched_query(
        conn,
        "SELECT DISTINCT source_id FROM edges WHERE target_id IN ({})",
        changed_node_ids,
    )
    pre_existing_caller_ids = [r[0] for r in caller_rows if r[0] not in changed_set]

    # Step 1: Delete outbound edges from changed nodes
    deleted = delete_outbound_edges(conn, changed_node_ids)
    if deleted:
        click.echo(f"[MAP] Deleted {deleted} outbound edges from changed nodes", err=True)

    # Step 2-3: Batched ripgrep + edge insertion
    edges_inserted = _process_matches(
        changed_nodes, rg_path, repo_root, repo_root_path, node_index, conn,
    )

    # Step 4: Re-resolve outbound edges from callers of changed nodes
    if pre_existing_caller_ids:
        click.echo(f"[MAP] Re-resolving {len(pre_existing_caller_ids)} callers of changed nodes", err=True)
        _batched_execute(
            conn,
            "DELETE FROM edges WHERE source_id IN ({})",
            pre_existing_caller_ids,
        )
        caller_nodes = [
            node_index.get(cid)
            for cid in pre_existing_caller_ids
            if node_index.get(cid) is not None
        ]
        edges_inserted += _process_matches(
            caller_nodes, rg_path, repo_root, repo_root_path, node_index, conn,
        )

    conn.commit()

    # Step 5: Purge dangling edges
    purged = purge_dangling_edges(conn)
    if purged:
        click.echo(f"[MAP] Purged {purged} dangling edges", err=True)
    conn.commit()

    # Step 6: Rebuild FTS5
    rebuild_fts(conn)
    click.echo(f"[MAP] Inserted {edges_inserted} edges, rebuilt FTS5", err=True)

    return edges_inserted
