# Jira Tickets: Hybrid Code Indexing System

**Project:** WIO
**Epic:** Code Indexing System
**Feature label:** `feature-code-indexing`
**Tech Spec:** `tech-spec-code-indexing.md`
**Implementation Plan:** `implementation-plan-code-indexing.md`
**Date:** March 25, 2026

> **Note:** These tickets are ready for creation in the WIO Jira project.
> Story Points: S=1, M=2, L=3.

---

## WIO-T1: Project scaffold and CLI skeleton

**Type:** Task
**Points:** 1 (S)
**Labels:** `feature-code-indexing`
**Blocked by:** none

---

### Context

Set up the Python package structure, `pyproject.toml`, and a `click`-based CLI skeleton with stub subcommands. This is the foundation task — all other tickets depend on this scaffold existing. The CLI entry point is `index`, with subcommands: `init`, `build`, `enrich`, `query`, `status`, `reset`.

See: `tech-spec-code-indexing.md` § Architecture (Component Responsibilities), § CLI Interface (Commands)

### Acceptance Criteria

- [ ] `pyproject.toml` defines the package with `click` as a dependency and `index` as the CLI entry point
- [ ] Package layout exists: `indexer/__init__.py`, `indexer/cli.py`, `indexer/db.py`, `indexer/parser.py`, `indexer/mapper.py`, `indexer/enricher.py`, `indexer/query.py`, `indexer/migrations/` directory
- [ ] `index --help` runs without error and lists all 6 commands
- [ ] Each command stub prints a `[TODO]` message to stderr and exits 0
- [ ] `--db PATH` global option is wired to all commands via `click.pass_context`
- [ ] `README.md` contains install instructions (`pip install -e .`)
- [ ] Smoke test passes: `index --help`, `index init --help`, `index build --help`
- [ ] No compiler warnings, no import errors

### Technical Notes

**Approach:** Use a `click` command group for `index` with `@cli.command()` subcommands. Pass the resolved DB path through a shared `Config` dataclass via `click.pass_context`.

**Patterns:**
- `[project.scripts] index = "indexer.cli:cli"` in `pyproject.toml`
- Global option pattern: `@click.pass_context` + `ctx.ensure_object(dict)` for shared state

**Files to create:**
- `pyproject.toml`
- `indexer/__init__.py`
- `indexer/cli.py` — click group + 6 stub subcommands
- `indexer/db.py` — empty stub
- `indexer/parser.py` — empty stub
- `indexer/mapper.py` — empty stub
- `indexer/enricher.py` — empty stub
- `indexer/query.py` — empty stub
- `indexer/migrations/.gitkeep`
- `tests/__init__.py`
- `tests/test_cli_smoke.py`

### Dependencies

- Blocked by: none
- Blocks: WIO-T2, WIO-T3, WIO-T4, WIO-T5, WIO-T6, WIO-T7, WIO-T8

### Out of Scope

- Any actual implementation beyond stubs
- DB schema creation (WIO-T2)
- Lock file logic (WIO-T5)

---

## WIO-T2: Database init, migration runner, and `index init` command

**Type:** Task
**Points:** 2 (M)
**Labels:** `feature-code-indexing`
**Blocked by:** WIO-T1

---

### Context

Implement the database bootstrap and migration system. Schema DDL lives in versioned SQL files under `indexer/migrations/`. `db.py` provides `bootstrap()` and `get_connection()`. The `index init` command is the explicit provisioning entry point; `index build` also calls `bootstrap()` automatically. This is the schema foundation — every other ticket depends on it.

See: `tech-spec-code-indexing.md` § Data Model (Schema, Indexes, Migration Strategy), § CLI Interface (Database Path Resolution, `index init`)

### Acceptance Criteria

- [ ] `indexer/migrations/001_initial.sql` contains full DDL: `nodes`, `edges`, `files`, `nodes_fts` (FTS5 with `content=nodes`), `index_meta` tables and all indexes from the spec
- [ ] `db.bootstrap(db_path)` creates `.codeindex/` directory if absent, creates DB file, runs all `NNN_*.sql` migrations in numeric order, sets `index_meta.schema_version` to highest migration applied
- [ ] `db.get_connection(db_path)` returns a `sqlite3.Connection` with `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys = ON` applied
- [ ] `db.resolve_db_path(db_arg)` follows 4-step resolution: `--db` → `CODEINDEX_DB` env var → `.codeindex/codeindex.db` → `sys.exit(2)` with actionable message
- [ ] `index init` is a no-op (exit 0) if schema version is current
- [ ] `index init` runs pending migrations if schema is stale (upgrade)
- [ ] `index init` exits 2 with `[ERROR] Schema version mismatch (DB: X, code: Y). Run: index reset --yes && index build` on downgrade scenario
- [ ] On first run, `index init` appends `.codeindex/` to `.gitignore` if not present, printing `[SETUP] Added .codeindex/ to .gitignore` to stderr. Suppressed by `--no-gitignore-update`.
- [ ] Unit tests cover: fresh DB create, no-op on re-run, migration upgrade, downgrade detection, gitignore append, path resolution order

### Technical Notes

**Approach:** Migration runner must use a `__file__`-relative path — NOT CWD-relative. Use `sorted((pathlib.Path(__file__).parent / "migrations").glob("*.sql"))` to locate migration files. This ensures the runner works correctly regardless of where `index` is invoked (project root, `/tmp`, Docker container, CI). A CWD-relative path like `glob.glob('indexer/migrations/*.sql')` silently finds nothing when invoked outside the project root. Compare sorted migration files against current `schema_version` in `index_meta` to determine which to apply.

**Key decisions from tech spec:**
- `PRAGMA foreign_keys = ON` must be set on every new connection — SQLite doesn't persist this setting
- `nodes_fts` uses `content=nodes, content_rowid=rowid` — this is external content FTS5. The table stores the index only, not the content. Requires manual `INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')` to sync.

**Files to create/modify:**
- `indexer/migrations/001_initial.sql` — full schema DDL + indexes
- `indexer/db.py` — `bootstrap()`, `get_connection()`, `resolve_db_path()`
- `indexer/cli.py` — wire `index init` command
- `tests/conftest.py` — shared `db_conn` pytest fixture: calls `db.bootstrap(':memory:')`, yields connection, closes it. All downstream test modules (test_parser, test_mapper, test_enricher, test_query, test_status_reset) use this fixture to avoid duplicating DB setup and filesystem coupling.
- `tests/test_db.py` — unit tests

### Dependencies

- Blocked by: WIO-T1
- Blocks: WIO-T3

### Out of Scope

- Node/edge data operations (WIO-T3, WIO-T4)
- Lock file (WIO-T5)

---

## WIO-T3a: Phase 1 — Python AST parser, incremental detection, and cAST chunking

**Type:** Story
**Points:** 2 (M)
**Labels:** `feature-code-indexing`
**Blocked by:** WIO-T2

---

### Context

Implement the Python parsing path for Phase 1: extract syntactically complete code nodes from Python files using `ast` stdlib, apply cAST chunking for oversized functions, detect changed files incrementally, and upsert into `nodes` and `files` tables. This ticket also defines the `parse_file()` and `parse_directory()` interfaces that WIO-T3b will extend for non-Python languages. Clearing `enriched_at` on content change is critical for correct hash-gating in WIO-T6.

See: `tech-spec-code-indexing.md` § Architecture (Phase 1 AST Parser), § Data Model (nodes table)

### Acceptance Criteria

- [ ] `parser.parse_file(path, conn)` extracts `file`, `class`, `function`, `method` nodes from Python files using `ast` stdlib
- [ ] Each extracted node has: `id` (stable format `{file_path}::{node_type}::{qualified_name}`), `name`, `qualified_name`, `signature`, `docstring`, `start_line`, `end_line`, `language`, `raw_source`, `content_hash` (SHA-256 of `raw_source`)
- [ ] `files` table is upserted with `content_hash`, `last_modified`, `language`, `node_count` for each parsed file
- [ ] Incremental build: files whose `content_hash` matches the stored value in `files` table are skipped entirely
- [ ] When upserting a node with a changed `content_hash`, `enriched_at` is cleared to NULL — this is the hash-gating mechanism for WIO-T6; without it, updated nodes will never be re-enriched
- [ ] Files matching `.gitignore` patterns and `*.db` inside `.codeindex/` are excluded
- [ ] cAST chunking: nodes whose estimated token count exceeds `--token-limit` (default 512) are split into syntactically complete subtrees; parent-child relationship preserved via `qualified_name` hierarchy
- [ ] Syntax error in a file: log `[WARNING] Skipped: {path} — {error}` to stderr; continue; exit code carries warning flag
- [ ] Unit tests: Python class + method extraction, incremental skip on unchanged file, `enriched_at` cleared when `content_hash` changes, cAST chunking of oversized function, `.gitignore` exclusion, syntax error handling

### Technical Notes

**Approach:** Use `ast.parse()` + `ast.walk()` to find `ClassDef`, `FunctionDef`, `AsyncFunctionDef` nodes. Extract signature from the function definition line; docstring from the first `ast.Constant` in the body if present.

**Token estimate:** `len(raw_source.split()) * 1.3` is conservative and sufficient for MVP. No tokenizer library required.

**Node ID stability:** The `id` must be identical across re-parses of unchanged nodes. Use file path relative to repo root (not absolute path).

**`enriched_at` clearing:** In the upsert SQL: `ON CONFLICT(id) DO UPDATE SET content_hash = excluded.content_hash, raw_source = excluded.raw_source, enriched_at = CASE WHEN content_hash != excluded.content_hash THEN NULL ELSE enriched_at END`. This is the contract that WIO-T6 depends on.

**Files to create/modify:**
- `indexer/parser.py` — `parse_file()`, `parse_directory()`, `chunk_node()`
- `tests/test_parser.py`
- `tests/fixtures/sample.py` — Python fixture with class, methods, and one oversized function

### Dependencies

- Blocked by: WIO-T2
- Blocks: WIO-T3b

### Out of Scope

- Kotlin and TypeScript parsing — that's WIO-T3b
- Dependency edge mapping — WIO-T4
- LLM enrichment fields — `semantic_summary`, `domain_tags`, `inferred_responsibility` are left NULL here

---

## WIO-T3b: Phase 1 — tree-sitter integration for Kotlin and TypeScript

**Type:** Story
**Points:** 2 (M)
**Labels:** `feature-code-indexing`
**Blocked by:** WIO-T3a

---

### Context

Extend the Phase 1 parser to handle Kotlin and TypeScript using `tree-sitter`. WIO-T3a defined the `parse_file()` interface and all shared infrastructure (incremental detection, cAST chunking, `enriched_at` clearing). This ticket adds language-specific grammar bindings for the two minimum required languages. Java and Go are explicitly deferred.

See: `tech-spec-code-indexing.md` § Architecture (Phase 1 AST Parser), § Implementation Stack

### Acceptance Criteria

- [ ] `parser.parse_file(path, conn)` handles Kotlin files using `tree-sitter-kotlin` grammar: extracts `class`, `function`, `method`, `interface`, `object` nodes
- [ ] `parser.parse_file(path, conn)` handles TypeScript files using `tree-sitter-typescript` grammar: extracts `class`, `function`, `method`, `interface` nodes
- [ ] Node fields are identical to Python nodes from WIO-T3a: all required fields populated, `enriched_at` cleared on content change
- [ ] cAST chunking from WIO-T3a applies to tree-sitter nodes without modification
- [ ] Unsupported languages (Java, Go, others) log `[WARNING] Unsupported language: {lang}, skipping {path}` to stderr and continue
- [ ] Unit tests: Kotlin class + method extraction, TypeScript class + function extraction, unsupported language warning path

### Technical Notes

**Grammar bindings:** Use `tree_sitter.Language` + `tree_sitter.Parser`. Grammars must be declared in `pyproject.toml`. tree-sitter compiles grammar bindings to native binaries on first install — this can take 30–60 seconds; document in README.

**Language detection:** Detect by file extension: `.kt` → Kotlin, `.ts`/`.tsx` → TypeScript, `.py` → Python (handled by T3a), anything else → unsupported warning.

**Files to create/modify:**
- `indexer/parser.py` — extend `parse_file()` with tree-sitter dispatch for Kotlin + TypeScript
- `pyproject.toml` — add `tree-sitter`, `tree-sitter-kotlin`, `tree-sitter-typescript`
- `tests/test_parser.py` — extend with Kotlin + TypeScript test cases
- `tests/fixtures/Sample.kt` — Kotlin fixture
- `tests/fixtures/sample.ts` — TypeScript fixture

### Dependencies

- Blocked by: WIO-T3a
- Blocks: WIO-T4

### Out of Scope

- Java and Go parsing (future ticket)
- Any changes to incremental detection, cAST chunking, or `enriched_at` logic — those are WIO-T3a's responsibility

---

## WIO-T4: Phase 2 — GrepRAG dependency mapper and FTS5 rebuild

**Type:** Story
**Points:** 2 (M)
**Labels:** `feature-code-indexing`
**Blocked by:** WIO-T3b

---

### Context

Implement Phase 2: using ripgrep to find call sites and import references for each changed node, resolve them to target node IDs, write directed edges to the `edges` table, and rebuild the FTS5 virtual table. Correct edge deletion scoping (outbound-only for changed nodes, dangling inbound for deleted/renamed nodes) is critical for incremental build correctness.

See: `tech-spec-code-indexing.md` § Architecture (Phase 2 Dependency Mapper), § Phase-Boundary Rebuild (steps 3a–3f)

### Acceptance Criteria

- [ ] `mapper.map_dependencies(changed_node_ids, conn, repo_root)` runs ripgrep for each changed node's exported identifiers; resolves file+line matches to node IDs via `nodes` table lookup
- [ ] Directed edges are written with correct `edge_type`: `calls`, `imports`, `inherits`, `overrides`, `references`, `instantiates`
- [ ] Edge deletion is scoped to outbound edges only from changed nodes: `DELETE FROM edges WHERE source_id IN (?)`
- [ ] Dangling inbound edges (to deleted/renamed nodes) are purged separately: `DELETE FROM edges WHERE target_id IN (?)`
- [ ] Callers of changed nodes have their outbound edges re-resolved (step 3e): re-run ripgrep for any node that previously called a changed node
- [ ] `nodes_fts` is rebuilt unconditionally at end of Phase 2: `INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')`
- [ ] If `ripgrep` is not found on PATH: exit 2 with `[ERROR] ripgrep not found. Install: https://github.com/BurntSushi/ripgrep#installation`
- [ ] Unit tests: edge insertion, outbound-only deletion correctness, dangling inbound purge, FTS5 rebuild called, ripgrep not-found error

### Technical Notes

**Ripgrep invocation:** `subprocess.run(["rg", "--json", "-n", identifier, repo_root], capture_output=True)` — parse JSON output for `{"type": "match", "data": {"path": ..., "line_number": ...}}`.

**FTS5 rebuild syntax:** `conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")` — this triggers a full sync of the external content FTS5 table from the `nodes` table.

**Import resolution:** Parse `import` statements in `raw_source`; map module name to file path using a simple project-local resolver (not full Python `sys.path` resolution for MVP).

**Files to create/modify:**
- `indexer/mapper.py` — `map_dependencies()`, `delete_outbound_edges()`, `purge_dangling_edges()`, `rebuild_fts()`
- `tests/test_mapper.py`

### Dependencies

- Blocked by: WIO-T3b
- Blocks: WIO-T5

### Out of Scope

- LLM enrichment (WIO-T6)
- Query interface (WIO-T7)

---

## WIO-T5: `index build` command — lock file, exit codes, and orchestration

**Type:** Story
**Points:** 1 (S)
**Labels:** `feature-code-indexing`
**Blocked by:** WIO-T4

---

### Context

Wire Phase 1 and Phase 2 together into the `index build` CLI command. Add auto-bootstrap (DB init if absent), a lock file guard to prevent concurrent builds, correct exit code propagation (0/1/2), and `index_meta` updates on completion. This is the main user-facing entry point for the pipeline orchestrator.

See: `tech-spec-code-indexing.md` § Phase-Boundary Rebuild (Build Execution, Trigger Points), § CLI Interface (Exit Codes, Output Streams)

### Acceptance Criteria

- [ ] `index build` calls `db.bootstrap()` automatically if DB does not exist before starting Phase 1
- [ ] Lock file acquired at `.codeindex/build.lock` (contains PID + ISO timestamp); released in a `finally` block
- [ ] Stale lock (older than 10 minutes): removed automatically with `[WARNING] Stale lock file removed. Previous build may have crashed.` to stderr
- [ ] Phase 1 then Phase 2 run in sequence; warnings collected and used for exit code determination
- [ ] `--phase PREPARE|DEPLOY` writes to `index_meta.last_phase_boundary`
- [ ] `index_meta` updated on completion: `last_full_build`, `total_nodes`, `total_edges`
- [ ] Exit 0: phases complete, no warnings; Exit 1: any files skipped or parse warnings; Exit 2: ripgrep not found, DB locked after retries, schema mismatch
- [ ] All phase progress goes to stderr: `[PHASE 1] Parsing N changed files...`, `[PHASE 2] Resolving edges...`
- [ ] Nothing written to stdout during a build run
- [ ] Integration test: run `index build` on small fixture repo; assert expected node/edge counts in DB

### Technical Notes

**Lock file:** Write `{"pid": os.getpid(), "started": datetime.utcnow().isoformat()}` to `.codeindex/build.lock`. Use `open(..., 'x')` (exclusive create) to detect concurrent builds — if file already exists and is fresh, print `[ERROR] Another build is running (PID: X). Exiting.` and exit 2.

**Files to create/modify:**
- `indexer/cli.py` — `index build` command implementation
- `tests/test_build.py` — integration test with fixture repo

### Dependencies

- Blocked by: WIO-T4
- Blocks: WIO-T6, WIO-T7, WIO-T8

### Out of Scope

- LLM enrichment — that's `index enrich` (WIO-T6)
- Query functionality (WIO-T7)

---

## WIO-T6: `index enrich` command — Phase 3 LLM enrichment

**Type:** Story
**Points:** 2 (M)
**Labels:** `feature-code-indexing`
**Blocked by:** WIO-T5

---

### Context

Implement Phase 3: LLM-driven semantic enrichment of index nodes. For each unenriched node (or node whose source has changed), build context from the graph (parent, children, callers, callees), call Claude API with the enrichment prompt, parse and store the result. Hash-gating prevents redundant API calls. Retry with exponential backoff handles transient failures.

See: `tech-spec-code-indexing.md` § `index enrich` Execution, § Enrichment Prompt, § Performance Considerations

### Acceptance Criteria

- [ ] `enricher.enrich_nodes(conn, model, dry_run)` selects nodes where `enriched_at IS NULL` — this is correct and sufficient because WIO-T3a clears `enriched_at` to NULL whenever `content_hash` changes during Phase 1, ensuring updated nodes are automatically re-queued for enrichment
- [ ] Before starting: prints `X nodes to enrich. Estimated time: ~Y minutes.` to stderr (Y = ceil(X / 60))
- [ ] `--dry-run`: exits after printing estimate, exit 0, zero API calls made
- [ ] For each node: builds context (signature + docstring + parent + children + callers + callees from `edges` table), calls Claude API, stores `semantic_summary`, `domain_tags` (parsed from JSON array), `inferred_responsibility`, `enriched_at` (ISO-8601), `enrichment_model`
- [ ] Enrichment prompt matches spec exactly (see `tech-spec-code-indexing.md` § Enrichment Prompt)
- [ ] Retry: exponential backoff on rate limit / timeout — waits 2^attempt seconds, max 3 attempts; on final failure node remains unenriched, `[WARNING] Enrichment failed for {qualified_name}: {error}` to stderr
- [ ] Malformed JSON from LLM: log warning, skip node, continue
- [ ] `--model MODEL` overrides default `claude-sonnet-4-6`
- [ ] After all nodes processed: updates `nodes_fts` for enriched nodes; updates `index_meta.unenriched_nodes`
- [ ] Exit 0 if all nodes enriched; exit 1 if any remain unenriched
- [ ] Unit tests: hash-gating (already-enriched unchanged node skipped), dry-run no API calls, malformed JSON handled, retry called on rate limit, FTS5 updated after enrich

### Technical Notes

**Claude API:** Use `anthropic` Python SDK. Key via `ANTHROPIC_API_KEY` env var — never stored in DB or config file. If key is absent, exit 2 with `[ERROR] ANTHROPIC_API_KEY not set.`

**Exponential backoff:** Catch `anthropic.RateLimitError` and `anthropic.APITimeoutError`; sleep `2 ** attempt` seconds before retry.

**FTS5 update after enrich:** For each enriched node, `DELETE FROM nodes_fts WHERE rowid = (SELECT rowid FROM nodes WHERE id = ?)` then `INSERT INTO nodes_fts(rowid, id, qualified_name, semantic_summary, domain_tags, inferred_responsibility) SELECT rowid, id, ... FROM nodes WHERE id = ?`.

**Files to create/modify:**
- `indexer/enricher.py` — `enrich_nodes()`, `build_node_context()`, `call_llm()`, `parse_enrichment_response()`
- `indexer/cli.py` — wire `index enrich` command
- `pyproject.toml` — add `anthropic` dependency
- `tests/test_enricher.py` (mock the Claude API client)

### Dependencies

- Blocked by: WIO-T5
- Blocks: none (semantic search in WIO-T7 degrades gracefully if enrichment not run)

### Out of Scope

- Query interface (WIO-T7)
- Batch enrichment strategies or cost optimisation beyond hash-gating

---

## WIO-T7: `index query` command — router, lexical, graph, and semantic search

**Type:** Story
**Points:** 2 (M)
**Labels:** `feature-code-indexing`
**Blocked by:** WIO-T5

---

### Context

Implement the query interface: a router that selects lexical, graph, or semantic search based on the query shape (or `--type` override), result dataclasses, and formatted stdout output. This is the primary interface used by pipeline agents to retrieve context from the index.

See: `tech-spec-code-indexing.md` § Query Interface

### Acceptance Criteria

- [ ] `query.route(input, hint)` selects lexical search for identifier-like input (no spaces, camelCase/snake_case), semantic for natural language; `--type` overrides
- [ ] `lexical_search(identifier, top_k)`: ripgrep exact match → node lookup by file+line → re-rank by specificity (rare identifiers and definition sites score higher) → return top-k `NodeResult`
- [ ] `graph_search(node_id, depth, edge_types, direction)`: recursive SQLite edge traversal (use `WITH RECURSIVE` CTE) up to `depth` hops; returns `GraphResult`
- [ ] `semantic_search(query, top_k)`: FTS5 BM25 query against `nodes_fts`; returns top-k `NodeResult` ranked by score
- [ ] Fallback routing: lexical → semantic if empty; semantic → lexical if empty
- [ ] `--with-source` includes `raw_source` in `NodeResult`; omitted by default
- [ ] `--format text|json|jsonl`: text for TTY (auto-detected via `sys.stdout.isatty()`), json (single array), jsonl (one object per line)
- [ ] All results written to stdout; all messages (routing decision, warnings) to stderr
- [ ] Exit 0 with results; exit 1 if index not found or semantic requested but no enrichment exists; exit 2 on DB error
- [ ] Unit tests: lexical match, semantic FTS5 query, graph traversal 2 hops, jsonl format, fallback routing, `--with-source` flag

### Technical Notes

**Recursive CTE for graph traversal:**
```sql
WITH RECURSIVE subgraph(id, depth) AS (
  SELECT ?, 0
  UNION ALL
  SELECT e.target_id, s.depth + 1
  FROM edges e JOIN subgraph s ON e.source_id = s.id
  WHERE s.depth < ?
)
SELECT n.* FROM nodes n JOIN subgraph s ON n.id = s.id
```

**Result dataclasses:** `NodeResult`, `GraphResult`, `EdgeResult` as defined in the spec. Use `dataclasses.asdict()` for JSON serialisation.

**Files to create/modify:**
- `indexer/query.py` — `QueryRouter`, `lexical_search()`, `graph_search()`, `semantic_search()`, result dataclasses
- `indexer/cli.py` — wire `index query` command
- `tests/test_query.py`

### Dependencies

- Blocked by: WIO-T5
- Blocks: none

### Out of Scope

- Interactive REPL mode (the "query interactively" mention in the spec is a future enhancement)
- Vector embeddings (explicitly out of scope in the spec)

---

## WIO-T8: `index status` and `index reset` commands

**Type:** Task
**Points:** 1 (S)
**Labels:** `feature-code-indexing`
**Blocked by:** WIO-T5

---

### Context

Implement the two operational commands: `index status` for index health inspection, and `index reset` for dropping and recreating the database. These are used by operators and the pipeline orchestrator to monitor and recover the index.

See: `tech-spec-code-indexing.md` § CLI Interface (Commands, Exit Codes, `index reset`)

### Acceptance Criteria

- [ ] `index status` prints to stdout: node count, edge count, unenriched node count, last build timestamp, last phase boundary, schema version, DB path
- [ ] `index status` exits 1 with `[INFO] Index not initialised. Run: index build` if DB does not exist
- [ ] `index status` prints `[WARNING] Schema version mismatch` with recovery instructions if version is stale
- [ ] `index reset` drops all tables and calls `db.bootstrap()` to recreate the schema
- [ ] `index reset` in non-TTY context (`not sys.stdin.isatty()`) requires `--yes`/`-y`; without it, exits 2 with `[ERROR] Destructive operation requires --yes flag. Add --yes to confirm.`
- [ ] `index reset` in interactive TTY: prompts `This will delete all indexed data. Continue? [y/N]`; abort on N
- [ ] All status output to stdout; reset confirmation prompt to stderr
- [ ] Unit tests: status output format, reset requires `--yes` in non-TTY, reset skips prompt with `--yes`

### Technical Notes

**Non-TTY detection:** `not sys.stdin.isatty()` — use this to decide whether to require `--yes`. In CI/pipelines stdin is never a TTY.

**Reset implementation:** `DROP TABLE IF EXISTS` for all tables in reverse dependency order (edges → nodes_fts → nodes → files → index_meta), then call `db.bootstrap()`. This reuses the same migration runner from T2.

**Files to create/modify:**
- `indexer/cli.py` — wire `index status` and `index reset`
- `tests/test_status_reset.py`

### Dependencies

- Blocked by: WIO-T5
- Blocks: none

### Out of Scope

- Metrics export or monitoring integration
- Any changes to `index build` or `index enrich` behaviour

---

## Dependency Summary

```
WIO-T1 ──▶ WIO-T2 ──▶ WIO-T3a ──▶ WIO-T3b ──▶ WIO-T4 ──▶ WIO-T5 ──▶ WIO-T6
                                                                  │
                                                                  ├──▶ WIO-T7
                                                                  └──▶ WIO-T8
```

| Ticket | Title | Points | Blocked by |
|--------|-------|--------|------------|
| WIO-T1 | Project scaffold and CLI skeleton | 1 | — |
| WIO-T2 | Database init, migration runner, `index init` | 2 | WIO-T1 |
| WIO-T3a | Phase 1 — Python AST parser, cAST, incremental detection | 2 | WIO-T2 |
| WIO-T3b | Phase 1 — tree-sitter for Kotlin + TypeScript | 2 | WIO-T3a |
| WIO-T4 | Phase 2 — GrepRAG dependency mapper and FTS5 | 2 | WIO-T3b |
| WIO-T5 | `index build` — lock file, exit codes, orchestration | 1 | WIO-T4 |
| WIO-T6 | `index enrich` — Phase 3 LLM enrichment | 2 | WIO-T5 |
| WIO-T7 | `index query` — router + lexical/graph/semantic | 2 | WIO-T5 |
| WIO-T8 | `index status` + `index reset` | 1 | WIO-T5 |
| **Total** | | **15** | |
