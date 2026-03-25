# Technical Spec: Hybrid Code Indexing System

**Status:** Draft
**Author:** Kris
**PRD Reference:** 02-prd.md (FR-16, FR-17, OQ-06)
**System Design Reference:** 03-system-design.md (Section 10)
**Last Updated:** March 25, 2026

---

## Overview

**Problem:** PREPARE and BUILD phase agents require structured knowledge of the codebase to reason about architecture and write correct, consistent code. Injecting raw source files into agent context windows is economically prohibitive, semantically noisy, and causes hallucinations at scale.

**Solution:** A three-phase hybrid indexing system — deterministic AST parsing + GrepRAG dependency mapping + LLM semantic enrichment — that builds a SQLite graph database of the codebase. Agents query the index rather than receiving raw code. The index is rebuilt at phase boundaries (before PREPARE, before DEPLOY), not on every commit.

**Scope:**
- In scope: index build pipeline, SQLite schema, query interface (lexical, graph, semantic), phase-boundary rebuild trigger, Python CLI
- Out of scope: agent prompt injection logic (covered in agent runner spec), IDE integration, real-time file watching, vector embeddings

---

## Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────────────┐
│                     SOURCE REPOSITORY                               │
│              (.kt, .ts, .py, .java, .go, ...)                       │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
              ┌─────────────▼──────────────┐
              │   PHASE 1 — AST PARSER     │  python ast / tree-sitter
              │                            │
              │  Extract: files, classes,  │
              │  functions, methods,       │
              │  signatures, docstrings,   │
              │  imports, line ranges      │
              │                            │
              │  cAST split-merge:         │
              │  recursive subtree         │
              │  decomposition within      │
              │  token size limits         │
              └─────────────┬──────────────┘
                            │
              ┌─────────────▼──────────────┐
              │  PHASE 2 — DEPENDENCY      │  ripgrep + import resolver
              │           MAPPER           │
              │                            │
              │  For each node: ripgrep    │
              │  all call sites and        │
              │  identifier references     │
              │                            │
              │  Resolve imports to        │
              │  source nodes              │
              │                            │
              │  Write directed edges:     │
              │  calls, imports, inherits, │
              │  overrides, references     │
              └─────────────┬──────────────┘
                            │
              ┌─────────────▼──────────────┐
              │  PHASE 3 — LLM ENRICHMENT  │  Claude API (hash-gated)
              │                            │
              │  Per node: send signature  │
              │  + docstring + immediate   │
              │  neighbours (parent,       │
              │  children, callers,        │
              │  callees)                  │
              │                            │
              │  Receive: semantic_summary,│
              │  domain_tags,              │
              │  inferred_responsibility   │
              │                            │
              │  Only re-runs on nodes     │
              │  where content_hash has    │
              │  changed since last build  │
              └─────────────┬──────────────┘
                            │
              ┌─────────────▼──────────────┐
              │  .codeindex/codeindex.db   │
              │      (SQLite)              │
              └─────────────┬──────────────┘
                            │
         ┌──────────────────┼──────────────────┐
         │                  │                  │
┌────────▼───────┐ ┌────────▼───────┐ ┌────────▼────────┐
│ LEXICAL QUERY  │ │  GRAPH QUERY   │ │ SEMANTIC QUERY  │
│                │ │                │ │                 │
│ ripgrep exact  │ │ SQLite edge    │ │ SQLite FTS on   │
│ identifier     │ │ traversal +    │ │ semantic_summary│
│ match +        │ │ adjacency      │ │ + domain_tags   │
│ re-ranking     │ │ expansion      │ │ match           │
└────────────────┘ └────────────────┘ └─────────────────┘
         │                  │                  │
         └──────────────────▼──────────────────┘
                    QUERY ROUTER
              (lexical → graph → semantic)
```

### Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| `indexer/parser.py` | Phase 1: AST traversal, node extraction, cAST chunking |
| `indexer/mapper.py` | Phase 2: GrepRAG dependency resolution, edge writing |
| `indexer/enricher.py` | Phase 3: LLM enrichment calls, hash-gating, result storage |
| `indexer/db.py` | SQLite connection management, bootstrap logic, migration runner |
| `indexer/migrations/001_initial.sql` | DDL for initial schema (nodes, edges, files, nodes_fts, index_meta) |
| `indexer/migrations/NNN_<description>.sql` | Future schema migrations, versioned by prefix |
| `indexer/query.py` | Query router: lexical, graph, semantic path selection |
| `indexer/cli.py` | CLI entrypoint: `index init`, `index build`, `index enrich`, `index query`, `index status`, `index reset` |
| `.codeindex/codeindex.db` | SQLite database, stored in `.codeindex/` subdirectory at project root — never committed to source control |

---

## Data Model

### Schema

```sql
-- Core node table: one row per syntactically complete code unit
CREATE TABLE nodes (
    id                      TEXT PRIMARY KEY,
    -- Format: {file_path}::{node_type}::{qualified_name}
    -- e.g. "src/services/CartService.kt::method::CartService.applyDiscount"

    file_path               TEXT NOT NULL,
    node_type               TEXT NOT NULL CHECK (node_type IN ('file', 'class', 'function', 'method', 'interface', 'object')),
    name                    TEXT NOT NULL,       -- unqualified name
    qualified_name          TEXT NOT NULL,       -- e.g. CartService.applyDiscount
    signature               TEXT,               -- full function/method signature with param types
    docstring               TEXT,               -- extracted doc comment if present
    start_line              INTEGER NOT NULL,
    end_line                INTEGER NOT NULL,
    language                TEXT NOT NULL,       -- 'kotlin' | 'typescript' | 'python' | 'java' | 'go'
    raw_source              TEXT,               -- full source of the AST subtree
    content_hash            TEXT NOT NULL,       -- SHA-256 of raw_source; gates Phase 3 re-enrichment

    -- LLM-enriched fields — NULL until Phase 3 runs for this node
    semantic_summary        TEXT,
    domain_tags             TEXT,               -- JSON array e.g. ["auth", "session", "jwt"]
    inferred_responsibility TEXT,
    enriched_at             TEXT,               -- ISO-8601 timestamp of last enrichment
    enrichment_model        TEXT                -- model used for enrichment e.g. 'claude-sonnet-4-6'
);

-- Dependency edge table: directed graph of code relationships
CREATE TABLE edges (
    source_id               TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id               TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    edge_type               TEXT NOT NULL CHECK (edge_type IN ('calls', 'imports', 'inherits', 'overrides', 'references', 'instantiates')),
    call_site_line          INTEGER,            -- line number where the relationship originates
    PRIMARY KEY (source_id, target_id, edge_type)
);

-- File registry: change detection and language tracking
CREATE TABLE files (
    path                    TEXT PRIMARY KEY,
    last_modified           TEXT NOT NULL,      -- ISO-8601 timestamp from filesystem
    content_hash            TEXT NOT NULL,      -- SHA-256 of full file content
    language                TEXT NOT NULL,
    node_count              INTEGER DEFAULT 0,  -- denormalised count for quick status display
    indexed_at              TEXT NOT NULL       -- ISO-8601 timestamp of last indexing
);

-- Full-text search virtual table for semantic queries
CREATE VIRTUAL TABLE nodes_fts USING fts5(
    id UNINDEXED,
    qualified_name,
    semantic_summary,
    domain_tags,
    inferred_responsibility,
    content=nodes,
    content_rowid=rowid
);

-- Index build metadata
CREATE TABLE index_meta (
    key                     TEXT PRIMARY KEY,
    value                   TEXT NOT NULL
);
-- Rows:
-- ('schema_version',           '1')
-- ('last_full_build',          '2026-03-25T10:00:00Z')
-- ('last_phase_boundary',      'PREPARE')  -- 'PREPARE' | 'DEPLOY'
-- ('enrichment_model',         'claude-sonnet-4-6')
-- ('total_nodes',              '1842')
-- ('total_edges',              '5231')
-- ('unenriched_nodes',         '0')
```

### Indexes

```sql
-- Node lookups
CREATE INDEX idx_nodes_file_path      ON nodes(file_path);
CREATE INDEX idx_nodes_name           ON nodes(name);
CREATE INDEX idx_nodes_qualified_name ON nodes(qualified_name);
CREATE INDEX idx_nodes_node_type      ON nodes(node_type);
CREATE INDEX idx_nodes_language       ON nodes(language);
CREATE INDEX idx_nodes_unenriched     ON nodes(enriched_at) WHERE enriched_at IS NULL;

-- Edge traversal (both directions needed for dependency graph)
CREATE INDEX idx_edges_source         ON edges(source_id);
CREATE INDEX idx_edges_target         ON edges(target_id);
CREATE INDEX idx_edges_type           ON edges(edge_type);
CREATE INDEX idx_edges_source_type    ON edges(source_id, edge_type);
CREATE INDEX idx_edges_target_type    ON edges(target_id, edge_type);
```

### Migration Strategy

**DB file is never shipped or committed.** The `.codeindex/` directory is always `.gitignore`d. The database is a derived artifact, fully regeneratable from source at any time.

**Bootstrap on install:** Schema DDL lives in versioned SQL migration files under `indexer/migrations/`, named `NNN_<description>.sql` (e.g. `001_initial.sql`). On first run, `db.py` detects the absence of `.codeindex/codeindex.db`, creates the directory, creates the database, and runs all migration files in numeric order. `index_meta.schema_version` is set to the highest migration number applied.

**Auto-bootstrap on `index build`:** If the database does not exist when `index build` is invoked, it is bootstrapped automatically before Phase 1 begins. This is the normal path for automated pipeline use — one command, it works, no separate provisioning step required.

**Explicit `index init` command:** Operators who need to pre-create the database before any source is available (e.g. in a Docker image build step or CI provisioning script) can run `index init` explicitly. If the database already exists and the schema version is current, `index init` is a no-op and exits 0. If the database exists but the schema version is stale, `index init` runs the pending migrations in order.

**Version mismatch at build time:** If `index build` or `index init` detects a schema version ahead of what the installed code knows about (downgrade scenario — e.g. switching to an older branch), it exits 2 with an actionable message printed to stderr:

```
[ERROR] Schema version mismatch (DB: 3, code: 1). Run: index reset --yes && index build
```

Because the index is fully derived from source, data loss on reset is not a concern.

### Database File Exclusions

The build process always excludes `codeindex.db` and any file matching `*.db` inside `.codeindex/` from Phase 1 parsing. Without this exclusion, Phase 1 would attempt to parse the binary SQLite file, fail with a parse warning, and continue — harmless but noisy.

On first run, the CLI checks for `.codeindex/` in the project root's `.gitignore`. If not present, it appends the entry automatically after printing a notice to stderr:

```
[SETUP] Added .codeindex/ to .gitignore to prevent committing the index database.
```

The operator can suppress this behaviour with `--no-gitignore-update` if they intentionally want to commit the database (e.g. for a shared read-only index on a CI server).

---

## Query Interface

### Query Router Logic

```
query(input: str, hint: QueryType?) → QueryResult

if hint == LEXICAL or input looks like an identifier (no spaces, camelCase/snake_case):
    → lexical_search(input)
    → if results empty: fall back to semantic_search(input)

elif hint == GRAPH:
    → graph_search(node_id, depth, edge_types)

elif hint == SEMANTIC or input contains spaces / natural language:
    → semantic_search(input)
    → if results empty: fall back to lexical_search(input)

else:
    → lexical_search(input)
    → if results empty: semantic_search(input)
```

### Lexical Search (GrepRAG path)

```python
def lexical_search(identifier: str, top_k: int = 10) -> list[NodeResult]:
    """
    1. Run ripgrep against raw source files for exact identifier match
    2. Collect matching file paths and line numbers
    3. Look up corresponding nodes in SQLite by file_path + line range
    4. Re-rank by identifier specificity:
       - Rare identifiers (few matches) score higher
       - Definition sites score higher than call sites
       - Exact name match scores higher than partial match
    5. Deduplicate nodes returning same qualified_name
    6. Return top_k nodes with signature + raw_source
    """
```

### Graph Search

```python
def graph_search(
    node_id: str,
    depth: int = 2,
    edge_types: list[str] = None,   # None = all edge types
    direction: str = "both"          # "outbound" | "inbound" | "both"
) -> GraphResult:
    """
    Recursive SQLite edge traversal up to `depth` hops.
    Returns nodes + edges as adjacency structure.
    Caller receives: summaries for all nodes (not raw_source),
    plus edge type labels for relationship context.
    """
```

### Semantic Search

```python
def semantic_search(query: str, top_k: int = 10) -> list[NodeResult]:
    """
    SQLite FTS5 query against nodes_fts virtual table.
    Searches: qualified_name, semantic_summary, domain_tags,
              inferred_responsibility.
    Returns top_k nodes ranked by BM25 score.
    Caller receives: summaries only (not raw_source) for initial results.
    Agent may request raw_source for specific nodes in a follow-up call.
    """
```

### Result Shape

```python
@dataclass
class NodeResult:
    id: str
    file_path: str
    node_type: str
    qualified_name: str
    signature: str | None
    docstring: str | None
    start_line: int
    end_line: int
    semantic_summary: str | None    # None if Phase 3 not yet run
    domain_tags: list[str]
    raw_source: str | None          # Only included when explicitly requested

@dataclass
class GraphResult:
    root_node: NodeResult
    nodes: list[NodeResult]         # All nodes in the subgraph
    edges: list[EdgeResult]         # All edges in the subgraph

@dataclass
class EdgeResult:
    source_id: str
    target_id: str
    edge_type: str
    call_site_line: int | None
```

---

## Phase-Boundary Rebuild

### Trigger Points

The index is rebuilt at two points in the pipeline lifecycle:

| Trigger | Phase boundary | Who invokes |
|---------|---------------|-------------|
| Card enters Architecture Spike | Before PREPARE | Pipeline orchestrator |
| Card enters QA | Before DEPLOY | Pipeline orchestrator |
| Operator demand | Any time | `index build` CLI command |

### Build Execution

```
index build [--phase PREPARE|DEPLOY] [--model MODEL]

1. For each file in repository (excluding .gitignore patterns
   and codeindex.db itself — see PR-008):
   a. Compute SHA-256 of file content
   b. Compare to stored hash in files table
   c. If changed or new: flag for Phase 1 re-parse

2. Phase 1 — AST parse flagged files:
   a. Parse via ast (Python) or tree-sitter (all other languages)
   b. Apply cAST split-merge: recursive subtree decomposition
      within configurable token limit (default: 512 tokens)
   c. Upsert nodes into SQLite; update files table

3. Phase 2 — Dependency mapping:
   a. Edge deletion scope — outbound edges only from changed nodes:
        DELETE FROM edges WHERE source_id IN (nodes from changed files)
      Inbound edges to changed nodes (i.e. callers from unchanged files)
      are retained — those callers have not changed and their edges remain
      valid. If a changed node was renamed or deleted, its inbound edges
      become dangling and must also be purged:
        DELETE FROM edges WHERE target_id IN (deleted/renamed node IDs)
   b. Run ripgrep pass for each changed node's exported identifiers
   c. Resolve import statements to target node IDs
   d. Insert new outbound edges from changed nodes
   e. Re-resolve outbound edges from any node that previously pointed INTO
      a changed node (its call target signature may have changed):
        Re-run ripgrep for callers of changed nodes; update their edges
   f. Rebuild nodes_fts virtual table (unconditional — always runs at end
      of Phase 2 to keep FTS in sync with structural changes regardless
      of whether Phase 3 enrichment is run)

4. Phase 3 — LLM enrichment (run via `index enrich`, not this command):
   Enrichment is a separate step. See `index enrich` command.

5. Update index_meta: last_full_build, last_phase_boundary, total counts
```

### `index enrich` Execution

```
index enrich [--model MODEL] [--dry-run] [--db PATH]

1. Select nodes WHERE content_hash != stored enrichment hash
   OR enriched_at IS NULL
2. Print to stderr: "X nodes to enrich. Estimated time: ~Y minutes."
3. For each unenriched node:
   a. Build context: signature + docstring + parent + children
      + callers (inbound 'calls' edges) + callees (outbound 'calls' edges)
   b. Call LLM with enrichment prompt (see below)
   c. Parse response: semantic_summary, domain_tags, inferred_responsibility
   d. Store result; update content_hash and enriched_at
4. Rebuild nodes_fts virtual table (targeted update for enriched nodes)
5. Update index_meta: unenriched_nodes count
6. Exit 0 if all nodes enriched; exit 1 if any nodes remain unenriched
```

### Enrichment Prompt

```
You are a code documentation assistant. Given the following code node and its
immediate context, provide structured metadata.

Node:
  Type: {node_type}
  Qualified name: {qualified_name}
  Signature: {signature}
  Docstring: {docstring or "none"}
  Source:
    {raw_source}

Context:
  Parent: {parent.qualified_name} — {parent.signature}
  Children: {[c.qualified_name for c in children]}
  Called by: {[c.qualified_name for c in callers]}
  Calls: {[c.qualified_name for c in callees]}

Respond in JSON only:
{
  "semantic_summary": "One to two sentences describing what this code does
                       and why it exists, in plain English.",
  "domain_tags": ["tag1", "tag2"],   // 2-5 lowercase domain concepts
  "inferred_responsibility": "Single sentence: what this code is responsible
                               for in the broader system."
}
```

---

## CLI Interface

### Output Streams

All progress logs, phase banners, warnings, and status messages are written to **stderr**. Only structured query results are written to **stdout**. This separation allows the pipeline orchestrator to capture query output via stdout redirection (`index query "..." > results.json`) without capturing noise, and allows operators to pipe query results to other tools cleanly.

```
stderr: [PHASE 1] Parsing 47 changed files...
stderr: [WARNING] Skipped: src/broken.kt — SyntaxError at line 12
stderr: [PHASE 2] Resolving edges... done (1,204 edges written)
stdout: (nothing during build — no structured output to capture)

stderr: Querying index for "validateCartState"...
stdout: {"id": "...", "qualified_name": "CartValidator.validateCartState", ...}
```

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | All phases completed successfully; no warnings |
| `1` | Completed with warnings — e.g. unenriched nodes remain, files skipped due to parse errors. Orchestrator should log and proceed. |
| `2` | Fatal failure — ripgrep not found, SQLite locked after all retries, schema version mismatch. Orchestrator must block and alert operator. |

### Database Path Resolution

The database path is resolved in this order:

```
1. --db PATH argument (highest priority)
2. CODEINDEX_DB environment variable
3. .codeindex/codeindex.db relative to current working directory
4. Error: exit 2 with message "Cannot determine database path.
          Set --db or CODEINDEX_DB, or run from the project root."
```

The `.codeindex/` subdirectory (rather than bare `codeindex.db` at root) avoids cluttering the project root and makes `.gitignore` management cleaner — a single `.codeindex/` entry excludes the whole directory.

### Commands

```
Usage: index <command> [options]

Commands:
  init        Create .codeindex/ directory and initialise the database schema.
              No-op if DB already exists and schema version is current.
              Runs pending migrations if schema is stale.
              Auto-invoked by `index build` if DB does not yet exist.
  build       Run Phases 1 and 2 (AST parse + dependency mapping).
              Bootstraps DB automatically if not yet initialised.
  enrich      Run Phase 3 only — LLM enrichment on unenriched nodes
  query       Query the index by argument or interactively
  status      Show index health: node count, edge count, unenriched nodes,
              last build time, schema version
  reset       Drop and recreate the database (requires --yes in
              non-interactive use)

Global options (all commands):
  --db PATH           Path to database file (overrides CODEINDEX_DB and default)

Options (build):
  --model MODEL       Override enrichment model used by subsequent `enrich` run
  --phase PHASE       Tag this build with a phase label (PREPARE | DEPLOY)
  --token-limit N     Max tokens per cAST chunk (default: 512)
  --exclude PATTERN   Additional gitignore-style exclusion patterns

Options (enrich):
  --model MODEL       Override enrichment model (default: claude-sonnet-4-6)
  --dry-run           Show how many nodes would be enriched; make no API calls

Options (query):
  --type TYPE         Force query type: lexical | graph | semantic
  --top-k N           Max results to return (default: 10)
  --depth N           Graph traversal depth for graph queries (default: 2)
  --with-source       Include raw_source in results
  --format FORMAT     Output format: text (default for TTY) | json | jsonl
                      json: single JSON array; jsonl: one object per line
                      (jsonl recommended for agent runner consumption)

Options (reset):
  --yes, -y           Skip interactive confirmation (required for
                      non-interactive / scripted use)

Options (init):
  (none beyond global --db)

Examples:
  # Pre-provision DB in a Docker image or CI setup step
  index init

  # Full build then enrich (auto-bootstraps DB if not present)
  index build --phase PREPARE
  index enrich --model claude-sonnet-4-6

  # Or combined via shell
  index build --phase PREPARE && index enrich

  # Query — human-readable
  index query "validateCartState" --type lexical --with-source

  # Query — agent runner (structured, stdout only)
  index query "cart loses items after discount" --type semantic --format jsonl

  # Non-interactive reset (scripted pipeline)
  index reset --yes && index build --phase PREPARE

  # Use explicit DB path
  index build --db /projects/myapp/.codeindex/codeindex.db --phase DEPLOY
```

---

## Error Handling

| Error scenario | Behaviour |
|---------------|-----------|
| File parse failure (syntax error in source) | Log warning; skip file; continue build. Node from previous build retained if present. |
| ripgrep not found on PATH | Phase 2 exits with clear error message pointing to installation docs. |
| LLM API call fails (rate limit, timeout) | Retry with exponential backoff (3 attempts). On final failure: log warning; node remains unenriched; build continues. Unenriched count reported in `index status`. |
| LLM returns malformed JSON | Log warning; skip enrichment for that node; node remains unenriched. |
| SQLite locked (concurrent access) | Retry with 500ms backoff up to 5 attempts. Only one index build runs at a time (lock file guard). |
| Schema version mismatch | Exit with error and instructions to run `index reset && index build`. |

---

## Performance Considerations

**Expected scale:** A single well-scoped project repository of 50–500 source files, producing 500–5,000 nodes. SQLite performs well within this range with no tuning required.

**Phase 1 + 2 speed:** Full parse and dependency map of a 500-file repository should complete in under 60 seconds. Incremental builds (only changed files) should complete in under 10 seconds.

**Phase 3 LLM cost and timing:** Enrichment cost is proportional to unenriched nodes, not total nodes. On a first build of 500 nodes, expect ~500 LLM calls at approximately 1 call/second (rate-limit dependent), giving a worst-case first-run enrichment time of **8–10 minutes**. Operators should be informed of this on first run; the `enrich` command outputs a time estimate to stderr before beginning. On an incremental build after a small ticket (3–5 changed files), expect 10–30 LLM calls, completing in under 60 seconds. Enrichment can be skipped entirely if cost is a concern — semantic summaries are not required for lexical or graph queries.

**Token budget for agents:** The query interface returns summaries by default and raw source only on explicit request. A PREPARE-phase architect query should consume fewer than 2,000 tokens of index context. A BUILD-phase lexical lookup should consume fewer than 500 tokens.

---

## Implementation Stack

| Concern | Tool | Justification |
|---------|------|---------------|
| Python AST parsing | `ast` (stdlib) | Zero dependencies for Python files |
| Multi-language parsing | `tree-sitter` + language grammars | Single API across Kotlin, TypeScript, Java, Go |
| Lexical search | `ripgrep` via subprocess | GrepRAG-validated; faster than Python grep implementations |
| Index store | `sqlite3` (stdlib) | Zero infrastructure; git-friendly; sufficient at project scale |
| Full-text search | SQLite FTS5 (bundled) | No additional dependency; adequate semantic search at this scale |
| LLM enrichment | Anthropic Claude API | Configurable; hash-gated to minimise calls |
| Content hashing | `hashlib` SHA-256 (stdlib) | Deterministic change detection; no external dependency |
| CLI | `click` or `argparse` (stdlib) | Lightweight; no framework overhead |

---

## Open Questions

| # | Question | Blocking? | Notes |
|---|----------|-----------|-------|
| OQ-I-01 | Which tree-sitter grammars are required for the project's language mix? | No | Depends on the target project. Kotlin and TypeScript likely minimum. |
| OQ-I-02 | Should Phase 3 enrichment run at PREPARE boundary or only at DEPLOY? | No | PREPARE gives richer context to the architect; adds LLM cost. Consider making it configurable per phase. |
| OQ-I-03 | How does the pipeline orchestrator invoke `index build`? Subprocess call or importable Python API? | No | CLI is the MVP interface; importable API is a clean-up task. |
| OQ-I-04 | What is the token limit per cAST chunk — 512 tokens is a starting point. Does it need to be tunable per language? | No | May need language-specific defaults as Kotlin is more verbose than Python. |

---

## Related Documents

- `02-prd.md` — FR-16 (codebase index available to BUILD/DEPLOY), FR-17 (index refreshed before DEPLOY), OQ-06 (index build and maintenance)
- `03-system-design.md` — Section 5.2 (Context Layer), Section 10 (Hybrid Code Indexing System)
- Scientific basis: cAST (arXiv:2506.15655v1), GrepRAG (ResearchGate/400340391), Hierarchical Summarisation (ResearchGate/391739021)
