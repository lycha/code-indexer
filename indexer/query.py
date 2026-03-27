"""Query router: lexical, graph, and semantic search."""

import json
import math
import re
import sqlite3
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import click

from indexer.utils import find_rg

__all__ = ["lexical_search", "semantic_search", "graph_search", "route_query", "format_results"]


@dataclass
class NodeResult:
    """A single node returned from a query."""

    id: str
    file_path: str
    node_type: str
    qualified_name: str
    signature: str | None
    docstring: str | None
    start_line: int
    end_line: int
    semantic_summary: str | None
    domain_tags: list[str]
    raw_source: str | None = None


@dataclass
class EdgeResult:
    """A single edge in the graph."""

    source_id: str
    target_id: str
    edge_type: str
    call_site_line: int | None


@dataclass
class GraphResult:
    """Result of a graph traversal query."""

    root_node: NodeResult
    nodes: list[NodeResult]
    edges: list[EdgeResult]


# ---------------------------------------------------------------------------
# Query Router
# ---------------------------------------------------------------------------

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*([.][A-Za-z_][A-Za-z0-9_]*)*$")
_CAMEL_RE = re.compile(r"[a-z][A-Z]")
_SNAKE_RE = re.compile(r"[a-z]_[a-z]")

_COMMON_IDENTIFIERS = frozenset({
    "get", "set", "put", "add", "run", "new", "map",
    "self", "this", "init", "main", "data", "name",
    "type", "value", "result", "error", "config",
    "test", "setup", "args", "kwargs", "params",
    "item", "items", "list", "dict", "key", "val",
    "start", "stop", "close", "open", "read", "write",
    "send", "save", "load", "update", "delete", "create",
    "path", "file", "node", "model", "index", "count",
    "size", "text", "line", "util", "utils", "helper",
})


def _looks_like_identifier(text: str) -> bool:
    """Return True if *text* looks like a code identifier (no spaces, camelCase/snake_case)."""
    if " " in text:
        return False
    if _IDENTIFIER_RE.match(text):
        return True
    if _CAMEL_RE.search(text) or _SNAKE_RE.search(text):
        return True
    return False


def route_query(query_text: str, query_type: str | None) -> str:
    """Determine which search strategy to use.

    Returns one of ``"lexical"``, ``"graph"``, ``"semantic"``.
    """
    if query_type is not None:
        return query_type
    if _looks_like_identifier(query_text):
        return "lexical"
    return "semantic"


# ---------------------------------------------------------------------------
# Lexical search  (ripgrep → node lookup → re-rank)
# ---------------------------------------------------------------------------

def lexical_search(
    identifier: str,
    conn: sqlite3.Connection,
    repo_root: str,
    top_k: int = 10,
    with_source: bool = False,
) -> list[NodeResult]:
    """Ripgrep exact word match → node lookup → re-rank by specificity.

    Ranking factors:
    - Definition-site bonus: +10 if the node name exactly matches the identifier
    - Specificity: 1 / total_match_count (fewer matches → higher weight)
    - Hit count: each hit in a node contributes +0.5
    - IDF weighting: log(total_files / (1 + matching_files)), capping at 0.1 minimum
    - Common identifier penalty: ×0.5 for identifiers in _COMMON_IDENTIFIERS
    """
    rg = find_rg()
    if rg is None:
        click.echo("[WARNING] ripgrep not found — lexical search unavailable", err=True)
        return []

    try:
        result = subprocess.run(
            [rg, "--json", "-n", "-w", "-F", identifier, repo_root],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        click.echo("[WARNING] ripgrep timed out — lexical search unavailable", err=True)
        return []

    matches: list[tuple[str, int]] = []
    repo_root_path = Path(repo_root)
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("type") != "match":
            continue
        md = data.get("data", {})
        fpath = md.get("path", {}).get("text", "")
        lnum = md.get("line_number", 0)
        if not fpath or not lnum:
            continue
        try:
            p = Path(fpath)
            rel = str(p.relative_to(repo_root_path)) if p.is_absolute() else fpath
        except ValueError:
            continue
        matches.append((rel, lnum))

    if not matches:
        return []

    # --- Batch-resolve matches → nodes (avoid N+1 queries) ----------------
    # 1. Collect unique file paths and fetch all candidate nodes in one query
    unique_paths = list({m[0] for m in matches})
    ph = ",".join("?" * len(unique_paths))
    candidate_rows = conn.execute(
        f"SELECT id, file_path, node_type, qualified_name, signature, docstring, "
        f"start_line, end_line, semantic_summary, domain_tags, raw_source, name "
        f"FROM nodes WHERE file_path IN ({ph})",
        unique_paths,
    ).fetchall()

    # 2. Build in-memory lookup: file_path → [(start, end, span, row), ...] sorted by span
    file_nodes: dict[str, list[tuple[int, int, int, tuple]]] = {}
    for row in candidate_rows:
        fp = row[1]
        start, end = row[6], row[7]
        span = end - start
        file_nodes.setdefault(fp, []).append((start, end, span, row))
    for entries in file_nodes.values():
        entries.sort(key=lambda e: e[2])  # smallest span first

    # 3. For each match, find enclosing node (smallest span) in-memory
    node_hits: dict[str, int] = {}
    for rel_path, lnum in matches:
        entries = file_nodes.get(rel_path)
        if not entries:
            continue
        for start, end, _span, row in entries:
            if start <= lnum <= end:
                nid = row[0]
                node_hits[nid] = node_hits.get(nid, 0) + 1
                break

    if not node_hits:
        return []

    total_matches = len(matches)

    # --- Frequency weighting (IDF) ---
    matching_files = len({m[0] for m in matches})
    total_files_row = conn.execute("SELECT COUNT(*) FROM files").fetchone()
    total_files = total_files_row[0] if total_files_row and total_files_row[0] > 0 else 1
    idf_weight = math.log(total_files / (1 + matching_files))
    # Floor IDF at a small positive value so it never zeroes out scores
    idf_weight = max(idf_weight, 0.1)

    # Common identifier penalty
    common_penalty = 0.5 if identifier.lower() in _COMMON_IDENTIFIERS else 1.0

    # 4. Build a lookup from rows we already fetched
    row_by_id: dict[str, tuple] = {row[0]: row for row in candidate_rows}

    # Fetch node data & score
    scored: list[tuple[float, NodeResult]] = []
    for nid, count in node_hits.items():
        row = row_by_id.get(nid)
        if not row:
            continue

        # Score: definition-site bonus + specificity
        score = 0.0
        name = row[11]
        if name == identifier:
            score += 10.0  # exact name match → likely definition
        # Specificity: fewer total matches → more specific
        score += 1.0 / max(total_matches, 1)
        # Hit count contribution
        score += count * 0.5
        # Apply frequency weighting
        score *= idf_weight * common_penalty

        tags = []
        if row[9]:
            try:
                tags = json.loads(row[9])
            except (json.JSONDecodeError, TypeError):
                pass

        nr = NodeResult(
            id=row[0],
            file_path=row[1],
            node_type=row[2],
            qualified_name=row[3],
            signature=row[4],
            docstring=row[5],
            start_line=row[6],
            end_line=row[7],
            semantic_summary=row[8],
            domain_tags=tags,
            raw_source=row[10] if with_source else None,
        )
        scored.append((score, nr))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [nr for _, nr in scored[:top_k]]


# ---------------------------------------------------------------------------
# Graph search  (recursive CTE)
# ---------------------------------------------------------------------------

def graph_search(
    node_id: str,
    conn: sqlite3.Connection,
    depth: int = 2,
    edge_types: list[str] | None = None,
    direction: str = "both",
    with_source: bool = False,
) -> GraphResult | None:
    """Recursive CTE graph traversal up to *depth* hops."""

    # Cap depth to avoid runaway exploration
    depth = min(depth, 10)

    # Verify root exists
    root_row = conn.execute(
        "SELECT id, file_path, node_type, qualified_name, signature, docstring, "
        "start_line, end_line, semantic_summary, domain_tags, raw_source "
        "FROM nodes WHERE id = ?",
        (node_id,),
    ).fetchone()
    if not root_row:
        return None

    def _make_node(row) -> NodeResult:
        tags = []
        if row[9]:
            try:
                tags = json.loads(row[9])
            except (json.JSONDecodeError, TypeError):
                pass
        return NodeResult(
            id=row[0], file_path=row[1], node_type=row[2], qualified_name=row[3],
            signature=row[4], docstring=row[5], start_line=row[6], end_line=row[7],
            semantic_summary=row[8], domain_tags=tags,
            raw_source=row[10] if with_source else None,
        )

    root_node = _make_node(root_row)

    # Build edge type filter clause
    et_clause = ""
    et_params: list = []
    if edge_types:
        placeholders = ",".join("?" * len(edge_types))
        et_clause = f" AND e.edge_type IN ({placeholders})"
        et_params = list(edge_types)

    # Build direction-aware recursive CTE
    if direction == "outbound":
        cte_join = "e.source_id = t.node_id"
        cte_select = "e.target_id"
    elif direction == "inbound":
        cte_join = "e.target_id = t.node_id"
        cte_select = "e.source_id"
    else:  # both
        cte_join = "(e.source_id = t.node_id OR e.target_id = t.node_id)"
        cte_select = "CASE WHEN e.source_id = t.node_id THEN e.target_id ELSE e.source_id END"

    sql = f"""
    WITH RECURSIVE traverse(node_id, hop) AS (
        SELECT ?, 0
        UNION
        SELECT {cte_select}, t.hop + 1
        FROM traverse t
        JOIN edges e ON {cte_join}{et_clause}
        WHERE t.hop < ?
    )
    SELECT DISTINCT node_id FROM traverse LIMIT 1000
    """
    params = [node_id] + et_params + [depth]
    visited_ids = {r[0] for r in conn.execute(sql, params).fetchall()}

    # Batch-fetch all visited nodes in a single query
    id_list = list(visited_ids)
    ph = ",".join("?" * len(id_list))
    node_rows = conn.execute(
        f"SELECT id, file_path, node_type, qualified_name, signature, docstring, "
        f"start_line, end_line, semantic_summary, domain_tags, raw_source "
        f"FROM nodes WHERE id IN ({ph})",
        id_list,
    ).fetchall()
    nodes: list[NodeResult] = [_make_node(row) for row in node_rows]

    # Fetch edges between visited nodes
    if len(visited_ids) < 2:
        edges: list[EdgeResult] = []
    else:
        id_list = list(visited_ids)
        ph = ",".join("?" * len(id_list))
        edge_rows = conn.execute(
            f"SELECT source_id, target_id, edge_type, call_site_line "
            f"FROM edges WHERE source_id IN ({ph}) AND target_id IN ({ph})",
            id_list + id_list,
        ).fetchall()
        edges = [EdgeResult(source_id=r[0], target_id=r[1], edge_type=r[2], call_site_line=r[3]) for r in edge_rows]

    return GraphResult(root_node=root_node, nodes=nodes, edges=edges)


# ---------------------------------------------------------------------------
# Semantic search  (FTS5 BM25)
# ---------------------------------------------------------------------------

def semantic_search(
    query: str,
    conn: sqlite3.Connection,
    top_k: int = 10,
    with_source: bool = False,
) -> list[NodeResult]:
    """FTS5 BM25 ranked search against nodes_fts."""
    # Check if any enriched nodes exist
    enriched_count = conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE enriched_at IS NOT NULL"
    ).fetchone()[0]
    if enriched_count == 0:
        click.echo("[WARNING] No enriched nodes — semantic search may return poor results", err=True)

    # Escape FTS5 special characters by quoting each term
    terms = query.split()
    fts_query = " OR ".join(f'"{t.replace(chr(34), chr(34)*2)}"' for t in terms)

    try:
        rows = conn.execute(
            "SELECT n.id, n.file_path, n.node_type, n.qualified_name, n.signature, "
            "n.docstring, n.start_line, n.end_line, n.semantic_summary, n.domain_tags, "
            "n.raw_source "
            "FROM nodes_fts f "
            "JOIN nodes n ON f.id = n.id "
            "WHERE nodes_fts MATCH ? "
            "ORDER BY rank "
            "LIMIT ?",
            (fts_query, top_k),
        ).fetchall()
    except Exception:
        return []

    results: list[NodeResult] = []
    for row in rows:
        tags = []
        if row[9]:
            try:
                tags = json.loads(row[9])
            except (json.JSONDecodeError, TypeError):
                pass
        results.append(NodeResult(
            id=row[0], file_path=row[1], node_type=row[2], qualified_name=row[3],
            signature=row[4], docstring=row[5], start_line=row[6], end_line=row[7],
            semantic_summary=row[8], domain_tags=tags,
            raw_source=row[10] if with_source else None,
        ))

    # Also search directory summaries (table may not exist in older DBs)
    try:
        dir_rows = conn.execute(
            "SELECT ds.dir_path, ds.summary, ds.domain_tags, ds.responsibility "
            "FROM dir_summaries_fts f JOIN directory_summaries ds ON f.dir_path = ds.dir_path "
            "WHERE dir_summaries_fts MATCH ? LIMIT ?",
            (fts_query, top_k),
        ).fetchall()
    except Exception:
        dir_rows = []

    for row in dir_rows:
        dir_path, summary, tags, responsibility = row
        try:
            tag_list = json.loads(tags) if tags else []
        except (json.JSONDecodeError, TypeError):
            tag_list = []
        dir_result = NodeResult(
            id=f"dir:{dir_path}",
            file_path=dir_path,
            node_type="directory",
            qualified_name=dir_path,
            signature=None,
            docstring=None,
            start_line=0,
            end_line=0,
            semantic_summary=summary,
            domain_tags=tag_list,
        )
        if with_source:
            dir_result.raw_source = responsibility or ""
        results.append(dir_result)

    return results[:top_k]


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

def _node_to_dict(n: NodeResult) -> dict:
    d = asdict(n)
    if d["raw_source"] is None:
        del d["raw_source"]
    return d


def _graph_to_dict(g: GraphResult) -> dict:
    return {
        "root_node": _node_to_dict(g.root_node),
        "nodes": [_node_to_dict(n) for n in g.nodes],
        "edges": [asdict(e) for e in g.edges],
    }


def format_results(
    results: list[NodeResult] | GraphResult | None,
    output_format: str,
) -> str:
    """Serialize results to the requested format string."""
    if results is None:
        if output_format == "json":
            return "[]"
        return ""

    if isinstance(results, GraphResult):
        if output_format == "json":
            return json.dumps(_graph_to_dict(results), indent=2)
        elif output_format == "jsonl":
            lines = [json.dumps(_node_to_dict(n)) for n in results.nodes]
            return "\n".join(lines)
        else:
            # text
            lines = [f"Root: {results.root_node.qualified_name} ({results.root_node.node_type})"]
            lines.append(f"Nodes: {len(results.nodes)}  Edges: {len(results.edges)}")
            for n in results.nodes:
                lines.append(f"  {n.node_type:10s} {n.qualified_name}")
            for e in results.edges:
                lines.append(f"  {e.source_id} --[{e.edge_type}]--> {e.target_id}")
            return "\n".join(lines)

    # list[NodeResult]
    node_list: list[NodeResult] = results  # type: ignore[assignment]
    if output_format == "json":
        return json.dumps([_node_to_dict(n) for n in node_list], indent=2)
    elif output_format == "jsonl":
        return "\n".join(json.dumps(_node_to_dict(n)) for n in node_list)
    else:
        # text
        lines: list[str] = []
        for n in node_list:
            header = f"{n.node_type:10s} {n.qualified_name}  ({n.file_path}:{n.start_line}-{n.end_line})"
            lines.append(header)
            if n.signature:
                lines.append(f"           {n.signature}")
            if n.semantic_summary:
                lines.append(f"           {n.semantic_summary}")
            if n.raw_source:
                lines.append(f"           [source included]")
        return "\n".join(lines)
