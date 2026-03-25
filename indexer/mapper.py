"""Phase 2: GrepRAG dependency mapping."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import click


def _find_rg() -> str:
    """Find ripgrep binary. Exit 2 if not found."""
    rg = shutil.which("rg")
    if rg is None:
        click.echo(
            "[ERROR] ripgrep not found. Install it: https://github.com/BurntSushi/ripgrep#installation",
            err=True,
        )
        sys.exit(2)
    return rg


def _get_changed_nodes(conn, changed_file_paths: list[str]) -> list[dict]:
    """Get all nodes belonging to changed files."""
    if not changed_file_paths:
        return []
    placeholders = ",".join("?" * len(changed_file_paths))
    rows = conn.execute(
        f"SELECT id, file_path, node_type, name, qualified_name, start_line, end_line "
        f"FROM nodes WHERE file_path IN ({placeholders})",
        changed_file_paths,
    ).fetchall()
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


def _resolve_match_to_node(conn, file_path: str, line: int) -> str | None:
    """Resolve a file+line match to a node ID via nodes table lookup.

    Find the most specific (smallest range) node containing this line.
    """
    row = conn.execute(
        "SELECT id FROM nodes "
        "WHERE file_path = ? AND start_line <= ? AND end_line >= ? "
        "ORDER BY (end_line - start_line) ASC LIMIT 1",
        (file_path, line, line),
    ).fetchone()
    return row[0] if row else None


def _classify_edge_type(source_node: dict, target_node_id: str, identifier: str, conn) -> str:
    """Classify the edge type based on source/target relationship.

    Heuristic classification:
    - imports: source is a file node
    - inherits: target is a class/interface and source is a class
    - instantiates: target is a class and source is a function/method
    - calls: target is a function/method
    - references: fallback
    """
    # Get target node info
    row = conn.execute(
        "SELECT node_type FROM nodes WHERE id = ?", (target_node_id,)
    ).fetchone()
    if not row:
        return "references"
    target_type = row[0]

    src_type = source_node["node_type"]

    if src_type == "file":
        return "imports"

    if target_type in ("class", "interface") and src_type in ("class", "interface"):
        return "inherits"

    if target_type == "class" and src_type in ("function", "method"):
        return "instantiates"

    if target_type in ("function", "method"):
        return "calls"

    return "references"


def _run_ripgrep(rg_path: str, identifier: str, repo_root: str) -> list[dict]:
    """Run ripgrep for an identifier, return parsed JSON matches."""
    result = subprocess.run(
        [rg_path, "--json", "-n", "-w", identifier, repo_root],
        capture_output=True,
        text=True,
    )
    matches = []
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
        if file_path and line_number:
            matches.append({"file": file_path, "line": line_number})
    return matches


def delete_outbound_edges(conn, changed_node_ids: list[str]) -> int:
    """Delete outbound edges from changed nodes only.

    Returns count of deleted edges.
    """
    if not changed_node_ids:
        return 0
    placeholders = ",".join("?" * len(changed_node_ids))
    cursor = conn.execute(
        f"DELETE FROM edges WHERE source_id IN ({placeholders})",
        changed_node_ids,
    )
    return cursor.rowcount


def purge_dangling_edges(conn) -> int:
    """Remove inbound edges to deleted/renamed nodes (target no longer exists).

    Returns count of purged edges.
    """
    cursor = conn.execute(
        "DELETE FROM edges WHERE target_id NOT IN (SELECT id FROM nodes)"
    )
    return cursor.rowcount


def rebuild_fts(conn) -> None:
    """Unconditionally rebuild nodes_fts virtual table."""
    conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")
    conn.commit()


def map_dependencies(changed_node_ids: list[str], conn, repo_root: str) -> int:
    """Map dependencies for changed nodes via ripgrep.

    Steps:
    1. Delete outbound edges from changed nodes
    2. Run ripgrep for each changed node's identifiers
    3. Resolve matches to node IDs, insert edges
    4. Re-resolve outbound edges from callers of changed nodes
    5. Purge dangling edges
    6. Rebuild FTS5

    Returns total edges inserted.
    """
    rg_path = _find_rg()
    repo_root_path = Path(repo_root)

    # Get changed node details
    if not changed_node_ids:
        rebuild_fts(conn)
        return 0

    # Fetch node info for changed nodes
    placeholders = ",".join("?" * len(changed_node_ids))
    rows = conn.execute(
        f"SELECT id, file_path, node_type, name, qualified_name, start_line, end_line "
        f"FROM nodes WHERE id IN ({placeholders})",
        changed_node_ids,
    ).fetchall()
    changed_nodes = [
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

    # Step 0: Collect callers of changed nodes BEFORE modifying edges
    # These are nodes that have outbound edges pointing TO changed nodes
    pre_existing_caller_ids = conn.execute(
        f"SELECT DISTINCT source_id FROM edges WHERE target_id IN ({placeholders}) "
        f"AND source_id NOT IN ({placeholders})",
        changed_node_ids + changed_node_ids,
    ).fetchall()
    pre_existing_caller_ids = [r[0] for r in pre_existing_caller_ids]

    # Step 1: Delete outbound edges from changed nodes
    deleted = delete_outbound_edges(conn, changed_node_ids)
    if deleted:
        click.echo(f"[MAP] Deleted {deleted} outbound edges from changed nodes", err=True)

    # Step 2-3: Run ripgrep and insert edges
    edges_inserted = 0
    for node in changed_nodes:
        identifiers = _get_exported_identifiers(node)
        for identifier in identifiers:
            matches = _run_ripgrep(rg_path, identifier, repo_root)
            for match in matches:
                # Convert absolute path to repo-relative
                match_path = match["file"]
                try:
                    match_p = Path(match_path)
                    if match_p.is_absolute():
                        rel_path = str(match_p.relative_to(repo_root_path))
                    else:
                        rel_path = match_path
                except ValueError:
                    continue

                # Don't create self-referencing edges to the same node
                source_node_id = _resolve_match_to_node(conn, rel_path, match["line"])
                if source_node_id is None:
                    continue
                if source_node_id == node["id"]:
                    continue

                # The match is where the identifier is USED, so source=match location, target=definition
                # source_node_id is where the identifier appears (the caller)
                # node["id"] is where the identifier is defined (the callee)
                source_info = conn.execute(
                    "SELECT node_type FROM nodes WHERE id = ?", (source_node_id,)
                ).fetchone()
                if not source_info:
                    continue
                source_dict = {"node_type": source_info[0]}
                edge_type = _classify_edge_type(source_dict, node["id"], identifier, conn)

                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO edges (source_id, target_id, edge_type, call_site_line) "
                        "VALUES (?, ?, ?, ?)",
                        (source_node_id, node["id"], edge_type, match["line"]),
                    )
                    edges_inserted += 1
                except Exception:
                    pass  # Ignore constraint violations

    # Step 4: Re-resolve outbound edges from callers of changed nodes
    caller_ids = pre_existing_caller_ids

    if caller_ids:
        click.echo(f"[MAP] Re-resolving {len(caller_ids)} callers of changed nodes", err=True)
        # Delete outbound edges from callers
        caller_placeholders = ",".join("?" * len(caller_ids))
        conn.execute(
            f"DELETE FROM edges WHERE source_id IN ({caller_placeholders})",
            caller_ids,
        )
        # Re-run mapping for callers
        caller_rows = conn.execute(
            f"SELECT id, file_path, node_type, name, qualified_name, start_line, end_line "
            f"FROM nodes WHERE id IN ({caller_placeholders})",
            caller_ids,
        ).fetchall()
        caller_nodes = [
            {
                "id": r[0],
                "file_path": r[1],
                "node_type": r[2],
                "name": r[3],
                "qualified_name": r[4],
                "start_line": r[5],
                "end_line": r[6],
            }
            for r in caller_rows
        ]
        for caller in caller_nodes:
            identifiers = _get_exported_identifiers(caller)
            for identifier in identifiers:
                matches = _run_ripgrep(rg_path, identifier, repo_root)
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
                    target_node_id = _resolve_match_to_node(conn, rel_path, match["line"])
                    if target_node_id is None or target_node_id == caller["id"]:
                        continue
                    edge_type = _classify_edge_type(caller, target_node_id, identifier, conn)
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO edges (source_id, target_id, edge_type, call_site_line) "
                            "VALUES (?, ?, ?, ?)",
                            (caller["id"], target_node_id, edge_type, match["line"]),
                        )
                        edges_inserted += 1
                    except Exception:
                        pass

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
