"""Query router: lexical, graph, and semantic search."""

import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import click


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

def _find_rg() -> str | None:
    """Find ripgrep binary. Returns *None* if not found."""
    return shutil.which("rg")


def lexical_search(
    identifier: str,
    conn,
    repo_root: str,
    top_k: int = 10,
    with_source: bool = False,
) -> list[NodeResult]:
    """Ripgrep exact word match → node lookup → re-rank by specificity."""
    rg = _find_rg()
    if rg is None:
        click.echo("[WARNING] ripgrep not found — lexical search unavailable", err=True)
        return []

    result = subprocess.run(
        [rg, "--json", "-n", "-w", identifier, repo_root],
        capture_output=True,
        text=True,
    )

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

    # Resolve matches → nodes, count occurrences per node for ranking
    node_hits: dict[str, int] = {}
    for rel_path, lnum in matches:
        row = conn.execute(
            "SELECT id FROM nodes "
            "WHERE file_path = ? AND start_line <= ? AND end_line >= ? "
            "ORDER BY (end_line - start_line) ASC LIMIT 1",
            (rel_path, lnum, lnum),
        ).fetchone()
        if row:
            nid = row[0]
            node_hits[nid] = node_hits.get(nid, 0) + 1

    if not node_hits:
        return []

    total_matches = len(matches)

    # Fetch node data & score
    scored: list[tuple[float, NodeResult]] = []
    for nid, count in node_hits.items():
        row = conn.execute(
            "SELECT id, file_path, node_type, qualified_name, signature, docstring, "
            "start_line, end_line, semantic_summary, domain_tags, raw_source, name "
            "FROM nodes WHERE id = ?",
            (nid,),
        ).fetchone()
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
    conn,
    depth: int = 2,
    edge_types: list[str] | None = None,
    direction: str = "both",
    with_source: bool = False,
) -> GraphResult | None:
    """Recursive CTE graph traversal up to *depth* hops."""

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
    SELECT DISTINCT node_id FROM traverse
    """
    params = [node_id] + et_params + [depth]
    visited_ids = {r[0] for r in conn.execute(sql, params).fetchall()}

    # Fetch all nodes
    nodes: list[NodeResult] = []
    for nid in visited_ids:
        row = conn.execute(
            "SELECT id, file_path, node_type, qualified_name, signature, docstring, "
            "start_line, end_line, semantic_summary, domain_tags, raw_source "
            "FROM nodes WHERE id = ?",
            (nid,),
        ).fetchone()
        if row:
            nodes.append(_make_node(row))

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
    conn,
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
    fts_query = " OR ".join(f'"{t}"' for t in terms)

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
    return results


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
