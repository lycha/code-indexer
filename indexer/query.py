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

__all__ = [
    "lexical_search", "semantic_search", "graph_search", "hybrid_search",
    "hierarchical_search", "hierarchical_search_llm", "route_query", "format_results",
    "DirectoryResult", "HierarchicalResult",
]


# ---------------------------------------------------------------------------
# LLM-driven hierarchical search prompt templates
# ---------------------------------------------------------------------------

DIRECTORY_SEARCH_PROMPT = """You are a code search expert. Given a query and directory summaries from a codebase, identify the most relevant directories.

## Query
{query}

## Project Overview
{project_summary}

## Directory Summaries
{directory_summaries}

Return a JSON array of the top {top_k} most relevant directory paths, ordered by relevance:
["dir1/path", "dir2/path", ...]

Return ONLY the JSON array, no explanation."""


FILE_SEARCH_PROMPT = """You are a code search expert. Given a query and file/node information from selected directories, identify the most relevant code locations.

## Query
{query}

## Files and Code Elements
{file_summaries}

Return a JSON array of the top {top_k} most relevant file paths, ordered by relevance:
["file1.py", "file2.py", ...]

Return ONLY the JSON array, no explanation."""


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


@dataclass
class DirectoryResult:
    """A directory from hierarchical navigation."""

    dir_path: str
    summary: str | None
    domain_tags: list[str]
    responsibility: str | None
    child_dirs: list[str]
    file_count: int
    node_count: int


@dataclass
class HierarchicalResult:
    """Result of a hierarchical drill-down query."""

    project_summary: str | None
    matched_directories: list[DirectoryResult]
    nodes: list[NodeResult]


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

    Returns one of ``"lexical"``, ``"graph"``, ``"semantic"``, ``"hybrid"``.
    """
    if query_type is not None:
        return query_type
    if _looks_like_identifier(query_text):
        return "lexical"
    # Hybrid: query has spaces AND contains at least one camelCase/snake_case/dotted identifier
    tokens = query_text.split()
    if len(tokens) > 1:
        has_structured_ident = any(
            _CAMEL_RE.search(t) or _SNAKE_RE.search(t) or ("." in t and _IDENTIFIER_RE.match(t))
            for t in tokens
        )
        if has_structured_ident:
            return "hybrid"
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
    escaped = [f'"{t.replace(chr(34), chr(34)*2)}"' for t in terms]

    def _run_fts_query(fts_query: str, limit: int) -> list[NodeResult]:
        """Execute an FTS5 MATCH query and return NodeResult list."""
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
                (fts_query, limit),
            ).fetchall()
        except Exception:
            return []

        out: list[NodeResult] = []
        for row in rows:
            tags = []
            if row[9]:
                try:
                    tags = json.loads(row[9])
                except (json.JSONDecodeError, TypeError):
                    pass
            out.append(NodeResult(
                id=row[0], file_path=row[1], node_type=row[2], qualified_name=row[3],
                signature=row[4], docstring=row[5], start_line=row[6], end_line=row[7],
                semantic_summary=row[8], domain_tags=tags,
                raw_source=row[10] if with_source else None,
            ))
        return out

    # Two-pass strategy: try AND first for tighter matching, fall back to OR
    results: list[NodeResult] = []
    if len(terms) > 1:
        fts_query_and = " AND ".join(escaped)
        results = _run_fts_query(fts_query_and, top_k)

    if len(results) < top_k:
        fts_query_or = " OR ".join(escaped)
        or_results = _run_fts_query(fts_query_or, top_k)
        # Prepend AND results, then append OR results (deduped)
        seen_ids = {r.id for r in results}
        for r in or_results:
            if r.id not in seen_ids:
                results.append(r)
                seen_ids.add(r.id)
        results = results[:top_k]

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
# Hybrid search  (lexical + semantic merge)
# ---------------------------------------------------------------------------

def hybrid_search(
    query: str,
    conn: sqlite3.Connection,
    repo_root: str,
    top_k: int = 10,
    with_source: bool = False,
) -> list[NodeResult]:
    """Merge lexical and semantic results for mixed queries.

    Splits the query into tokens, runs lexical_search for each identifier-like
    token and semantic_search for the full query, then merges and deduplicates
    results by node id (keeping the higher-scoring occurrence).
    """
    tokens = query.split()
    ident_tokens = [t for t in tokens if _looks_like_identifier(t)]

    # Collect lexical results for each identifier-like token
    seen: dict[str, NodeResult] = {}
    for token in ident_tokens:
        lex_results = lexical_search(
            identifier=token, conn=conn, repo_root=repo_root,
            top_k=top_k, with_source=with_source,
        )
        for nr in lex_results:
            if nr.id not in seen:
                seen[nr.id] = nr

    # Run semantic search with the full query
    sem_results = semantic_search(
        query=query, conn=conn, top_k=top_k, with_source=with_source,
    )
    for nr in sem_results:
        if nr.id not in seen:
            seen[nr.id] = nr

    # Merge: lexical results first (definition-site matches), then semantic
    merged: list[NodeResult] = []
    added_ids: set[str] = set()

    # Lexical results preserve their ranking order
    for token in ident_tokens:
        lex_results = lexical_search(
            identifier=token, conn=conn, repo_root=repo_root,
            top_k=top_k, with_source=with_source,
        )
        for nr in lex_results:
            if nr.id not in added_ids:
                merged.append(nr)
                added_ids.add(nr.id)

    # Append semantic results that weren't already added
    for nr in sem_results:
        if nr.id not in added_ids:
            merged.append(nr)
            added_ids.add(nr.id)

    return merged[:top_k]


# ---------------------------------------------------------------------------
# Hierarchical search  (top-down navigation)
# ---------------------------------------------------------------------------

def hierarchical_search(
    query: str,
    conn: sqlite3.Connection,
    top_k: int = 10,
    with_source: bool = False,
) -> HierarchicalResult:
    """Top-down hierarchical drill-down: project → directory → nodes.

    1. Start with the project summary (dir_path='.') for context.
    2. Search dir_summaries_fts for directories matching the query.
    3. For each matched directory, gather child dirs, file/node counts.
    4. For the top matched directories, fetch relevant nodes.
    """
    # 1. Project summary
    project_summary: str | None = None
    try:
        row = conn.execute(
            "SELECT summary FROM directory_summaries WHERE dir_path = '.'",
        ).fetchone()
        if row:
            project_summary = row[0]
    except Exception:
        pass

    # 2. Search directories via FTS5 (AND-then-OR strategy)
    terms = query.split()
    escaped = [f'"{t.replace(chr(34), chr(34)*2)}"' for t in terms]

    def _run_dir_fts(fts_query: str, limit: int) -> list[tuple]:
        try:
            return conn.execute(
                "SELECT ds.dir_path, ds.summary, ds.domain_tags, ds.responsibility "
                "FROM dir_summaries_fts f "
                "JOIN directory_summaries ds ON f.dir_path = ds.dir_path "
                "WHERE dir_summaries_fts MATCH ? "
                "ORDER BY rank "
                "LIMIT ?",
                (fts_query, limit),
            ).fetchall()
        except Exception:
            return []

    dir_rows: list[tuple] = []
    if len(terms) > 1:
        fts_and = " AND ".join(escaped)
        dir_rows = _run_dir_fts(fts_and, 5)

    if len(dir_rows) < 5:
        fts_or = " OR ".join(escaped)
        or_rows = _run_dir_fts(fts_or, 5)
        seen_paths = {r[0] for r in dir_rows}
        for r in or_rows:
            if r[0] not in seen_paths:
                dir_rows.append(r)
                seen_paths.add(r[0])
        dir_rows = dir_rows[:5]

    # 3. Build DirectoryResult for each matched directory
    matched_directories: list[DirectoryResult] = []
    for dir_path, summary, raw_tags, responsibility in dir_rows:
        tags: list[str] = []
        if raw_tags:
            try:
                tags = json.loads(raw_tags)
            except (json.JSONDecodeError, TypeError):
                pass

        # Child directories (immediate children only)
        child_rows = conn.execute(
            "SELECT dir_path FROM directory_summaries "
            "WHERE dir_path LIKE ? AND dir_path != ? AND dir_path NOT LIKE ?",
            (f"{dir_path}/%", dir_path, f"{dir_path}/%/%"),
        ).fetchall()
        child_dirs = [r[0] for r in child_rows]

        # File and node counts
        counts = conn.execute(
            "SELECT COUNT(DISTINCT file_path), COUNT(*) FROM nodes WHERE file_path LIKE ?",
            (f"{dir_path}/%",),
        ).fetchone()
        file_count = counts[0] if counts else 0
        node_count = counts[1] if counts else 0

        matched_directories.append(DirectoryResult(
            dir_path=dir_path,
            summary=summary,
            domain_tags=tags,
            responsibility=responsibility,
            child_dirs=child_dirs,
            file_count=file_count,
            node_count=node_count,
        ))

    # 4. Fetch nodes from top 3 matched directories
    nodes: list[NodeResult] = []
    seen_ids: set[str] = set()
    for dr in matched_directories[:3]:
        node_rows = conn.execute(
            "SELECT id, file_path, node_type, qualified_name, signature, docstring, "
            "start_line, end_line, semantic_summary, domain_tags, raw_source "
            "FROM nodes WHERE file_path LIKE ? "
            "ORDER BY node_type, qualified_name "
            "LIMIT ?",
            (f"{dr.dir_path}/%", top_k),
        ).fetchall()
        for row in node_rows:
            nid = row[0]
            if nid in seen_ids:
                continue
            seen_ids.add(nid)
            node_tags: list[str] = []
            if row[9]:
                try:
                    node_tags = json.loads(row[9])
                except (json.JSONDecodeError, TypeError):
                    pass
            nodes.append(NodeResult(
                id=row[0],
                file_path=row[1],
                node_type=row[2],
                qualified_name=row[3],
                signature=row[4],
                docstring=row[5],
                start_line=row[6],
                end_line=row[7],
                semantic_summary=row[8],
                domain_tags=node_tags,
                raw_source=row[10] if with_source else None,
            ))

    return HierarchicalResult(
        project_summary=project_summary,
        matched_directories=matched_directories,
        nodes=nodes[:top_k],
    )


# ---------------------------------------------------------------------------
# LLM-driven hierarchical search  (paper's inference phase)
# ---------------------------------------------------------------------------

def _parse_llm_json_array(response: str) -> list[str]:
    """Defensively parse a JSON array from an LLM response.

    Handles markdown code fences, extra text around the array, etc.
    Returns an empty list on failure.
    """
    # Strip markdown code fences if present
    cleaned = re.sub(r'^```(?:json)?\s*', '', response.strip())
    cleaned = re.sub(r'\s*```$', '', cleaned)
    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return [str(item) for item in result]
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: try to extract an array pattern from the response
    match = re.search(r'\[.*\]', response, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return [str(item) for item in result]
        except (json.JSONDecodeError, ValueError):
            pass
    return []


def hierarchical_search_llm(
    query: str,
    conn: sqlite3.Connection,
    top_k: int = 10,
    with_source: bool = False,
    model: str | None = None,
    provider: str | None = None,
) -> HierarchicalResult:
    """LLM-driven top-down hierarchical search matching the paper's inference phase.

    Step 1: LLM reads all directory summaries + query → selects top-k directories
    Step 2: LLM reads file/node summaries from selected directories → ranks files
    Step 3: Return matching nodes from the ranked files

    Falls back to FTS5-based hierarchical_search() if LLM calls fail.
    """
    from indexer.enricher import DEFAULT_MODELS, DEFAULT_PROVIDER, call_llm

    if provider is None:
        provider = DEFAULT_PROVIDER
    if model is None:
        model = DEFAULT_MODELS.get(provider, "gemini-2.5-flash-lite")

    # Step 1: Get project summary
    project_summary = "No project summary available."
    try:
        project_row = conn.execute(
            "SELECT summary FROM directory_summaries WHERE dir_path = '.'",
        ).fetchone()
        if project_row and project_row[0]:
            project_summary = project_row[0]
    except Exception:
        pass

    # Step 2: Get all directory summaries
    try:
        dir_rows = conn.execute(
            "SELECT dir_path, summary, domain_tags, responsibility "
            "FROM directory_summaries WHERE dir_path != '.' ORDER BY dir_path",
        ).fetchall()
    except Exception:
        dir_rows = []

    if not dir_rows:
        # No directory summaries available — fall back to FTS5-based search
        return hierarchical_search(query, conn, top_k=top_k, with_source=with_source)

    # Format directory summaries for the prompt
    dir_text_parts = []
    for i, (path, summary, tags, resp) in enumerate(dir_rows, 1):
        dir_text_parts.append(
            f"{i}. {path}/\n   Summary: {summary or 'N/A'}\n   Tags: {tags or 'N/A'}"
        )
    directory_summaries_text = "\n".join(dir_text_parts)

    # Step 3: Call LLM for directory selection
    dir_prompt = DIRECTORY_SEARCH_PROMPT.format(
        query=query,
        project_summary=project_summary,
        directory_summaries=directory_summaries_text,
        top_k=min(5, len(dir_rows)),
    )

    try:
        dir_response = call_llm(dir_prompt, model=model, provider=provider)
        selected_dirs = _parse_llm_json_array(dir_response)
    except Exception:
        selected_dirs = []

    if not selected_dirs:
        # LLM failed or returned empty — fall back to FTS5
        return hierarchical_search(query, conn, top_k=top_k, with_source=with_source)

    # Step 4: Get file/node summaries from selected directories
    file_text_parts = []
    for dir_path in selected_dirs[:5]:
        # Normalize: strip trailing slash if present
        dir_path = dir_path.rstrip("/")

        # Try file-level summaries first (file-type nodes)
        file_rows = conn.execute(
            "SELECT file_path, semantic_summary, domain_tags FROM nodes "
            "WHERE file_path LIKE ? AND node_type = 'file' AND enriched_at IS NOT NULL "
            "ORDER BY file_path",
            (f"{dir_path}/%",),
        ).fetchall()

        if file_rows:
            for fp, summary, tags in file_rows:
                file_text_parts.append(f"- {fp}: {summary or 'No summary'} [{tags or ''}]")
        else:
            # Fallback to individual node summaries
            node_rows = conn.execute(
                "SELECT file_path, qualified_name, node_type, semantic_summary FROM nodes "
                "WHERE file_path LIKE ? AND node_type != 'file' AND enriched_at IS NOT NULL "
                "ORDER BY file_path, qualified_name LIMIT 50",
                (f"{dir_path}/%",),
            ).fetchall()
            for fp, qn, nt, summary in node_rows:
                file_text_parts.append(f"- {fp} :: {qn} ({nt}): {summary or 'No summary'}")

    if not file_text_parts:
        # No file info found — fall back to FTS5
        return hierarchical_search(query, conn, top_k=top_k, with_source=with_source)

    # Step 5: Call LLM for file ranking
    file_prompt = FILE_SEARCH_PROMPT.format(
        query=query,
        file_summaries="\n".join(file_text_parts),
        top_k=top_k,
    )

    try:
        file_response = call_llm(file_prompt, model=model, provider=provider)
        ranked_files = _parse_llm_json_array(file_response)
    except Exception:
        ranked_files = []

    # Step 6: Build HierarchicalResult
    # Build DirectoryResult objects for selected directories
    matched_directories: list[DirectoryResult] = []
    for dir_path in selected_dirs[:5]:
        dir_path = dir_path.rstrip("/")

        # Get directory summary from DB
        dir_info = conn.execute(
            "SELECT summary, domain_tags, responsibility FROM directory_summaries WHERE dir_path = ?",
            (dir_path,),
        ).fetchone()

        if not dir_info:
            continue

        summary, raw_tags, responsibility = dir_info
        tags: list[str] = []
        if raw_tags:
            try:
                tags = json.loads(raw_tags)
            except (json.JSONDecodeError, TypeError):
                pass

        # Child directories (immediate children only)
        child_rows = conn.execute(
            "SELECT dir_path FROM directory_summaries "
            "WHERE dir_path LIKE ? AND dir_path != ? AND dir_path NOT LIKE ?",
            (f"{dir_path}/%", dir_path, f"{dir_path}/%/%"),
        ).fetchall()
        child_dirs = [r[0] for r in child_rows]

        # File and node counts
        counts = conn.execute(
            "SELECT COUNT(DISTINCT file_path), COUNT(*) FROM nodes WHERE file_path LIKE ?",
            (f"{dir_path}/%",),
        ).fetchone()
        file_count = counts[0] if counts else 0
        node_count = counts[1] if counts else 0

        matched_directories.append(DirectoryResult(
            dir_path=dir_path,
            summary=summary,
            domain_tags=tags,
            responsibility=responsibility,
            child_dirs=child_dirs,
            file_count=file_count,
            node_count=node_count,
        ))

    # Fetch nodes from ranked files
    nodes: list[NodeResult] = []
    seen_ids: set[str] = set()
    for file_path in ranked_files[:top_k]:
        rows = conn.execute(
            "SELECT id, file_path, node_type, qualified_name, signature, docstring, "
            "start_line, end_line, semantic_summary, domain_tags, raw_source "
            "FROM nodes WHERE file_path = ? ORDER BY start_line",
            (file_path,),
        ).fetchall()
        for row in rows:
            nid = row[0]
            if nid in seen_ids:
                continue
            seen_ids.add(nid)
            node_tags: list[str] = []
            if row[9]:
                try:
                    node_tags = json.loads(row[9])
                except (json.JSONDecodeError, TypeError):
                    pass
            nodes.append(NodeResult(
                id=row[0],
                file_path=row[1],
                node_type=row[2],
                qualified_name=row[3],
                signature=row[4],
                docstring=row[5],
                start_line=row[6],
                end_line=row[7],
                semantic_summary=row[8],
                domain_tags=node_tags,
                raw_source=row[10] if with_source else None,
            ))

    return HierarchicalResult(
        project_summary=project_summary,
        matched_directories=matched_directories,
        nodes=nodes[:top_k],
    )


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


def _hierarchical_to_dict(h: HierarchicalResult) -> dict:
    dirs = []
    for d in h.matched_directories:
        dirs.append({
            "dir_path": d.dir_path,
            "summary": d.summary,
            "domain_tags": d.domain_tags,
            "responsibility": d.responsibility,
            "child_dirs": d.child_dirs,
            "file_count": d.file_count,
            "node_count": d.node_count,
        })
    return {
        "project_summary": h.project_summary,
        "matched_directories": dirs,
        "nodes": [_node_to_dict(n) for n in h.nodes],
    }


def format_results(
    results: list[NodeResult] | GraphResult | HierarchicalResult | None,
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

    if isinstance(results, HierarchicalResult):
        if output_format == "json":
            return json.dumps(_hierarchical_to_dict(results), indent=2)
        elif output_format == "jsonl":
            lines: list[str] = []
            for d in results.matched_directories:
                lines.append(json.dumps({
                    "type": "directory",
                    "dir_path": d.dir_path,
                    "summary": d.summary,
                    "domain_tags": d.domain_tags,
                    "responsibility": d.responsibility,
                    "child_dirs": d.child_dirs,
                    "file_count": d.file_count,
                    "node_count": d.node_count,
                }))
            for n in results.nodes:
                lines.append(json.dumps(_node_to_dict(n)))
            return "\n".join(lines)
        else:
            # text
            lines = []
            if results.project_summary:
                lines.append("=== Project Overview ===")
                lines.append(results.project_summary)
                lines.append("")

            if results.matched_directories:
                lines.append("=== Matched Directories ===")
                lines.append("")
                for i, d in enumerate(results.matched_directories, 1):
                    lines.append(f"[{i}] {d.dir_path}/")
                    if d.summary:
                        lines.append(f"    Summary: {d.summary}")
                    if d.domain_tags:
                        lines.append(f"    Tags: {', '.join(d.domain_tags)}")
                    if d.responsibility:
                        lines.append(f"    Responsibility: {d.responsibility}")
                    if d.child_dirs:
                        lines.append(f"    Children: {', '.join(d.child_dirs)}")
                    lines.append(f"    Files: {d.file_count}  Nodes: {d.node_count}")
                    lines.append("")

            if results.nodes:
                lines.append("=== Relevant Nodes (from matched directories) ===")
                for n in results.nodes:
                    header = f"{n.node_type:10s} {n.qualified_name}  ({n.file_path}:{n.start_line}-{n.end_line})"
                    lines.append(header)
                    if n.signature:
                        lines.append(f"           {n.signature}")
                    if n.semantic_summary:
                        lines.append(f"           {n.semantic_summary}")
                    if n.raw_source:
                        lines.append(f"           [source included]")

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
