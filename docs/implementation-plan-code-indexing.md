# Implementation Plan: Hybrid Code Indexing System

**Tech Spec:** `tech-spec-code-indexing.md`
**Date:** March 25, 2026
**Total Tasks:** 9
**Estimated Duration:** 6–8 days on critical path (sequential)

---

## Dependency Graph

```
T1 (S) ──▶ T2 (M) ──▶ T3a (M) ──▶ T3b (M) ──▶ T4 (M) ──▶ T5 (S) ──▶ T6 (M) ──▶ T7 (M) ──▶ T8 (S)
                                                                                                        ⭐ critical path
```

All tasks are sequential — each phase builds on the one before it. The critical path runs T1 → T2 → T3a → T3b → T4 → T5 → T6 → T7 → T8.

**Note:** T7 (query) and T8 (status/reset) can be worked in parallel after T5 is complete if two engineers are available. In single-engineer sequential execution, follow the order below.

---

## Tasks

### T1: Project scaffold and CLI skeleton ⭐ critical path
- **Size:** S (1 point)
- **Type:** Task
- **Depends on:** none (foundation)
- **Acceptance Criteria:**
  - `pyproject.toml` defines the package with `click` dependency and `index` as the CLI entry point
  - Package layout exists: `indexer/__init__.py`, `indexer/cli.py`, `indexer/db.py`, `indexer/parser.py`, `indexer/mapper.py`, `indexer/enricher.py`, `indexer/query.py`, `indexer/migrations/` directory
  - `index --help` runs without error and lists all commands: `init`, `build`, `enrich`, `query`, `status`, `reset`
  - Each command stub prints a `[TODO]` message and exits 0
  - `--db PATH` global option is wired to all commands
  - `README.md` contains install instructions (`pip install -e .`)
  - All stub commands pass a basic smoke test (`subprocess.run(["index", "--help"])`)
- **Technical Notes:**
  - Use `click` groups for the top-level `index` command with subcommands
  - Global `--db` option should be passed via `click.pass_context` or a shared `Config` object
  - Package entrypoint: `[project.scripts] index = "indexer.cli:cli"`
- **Files to Create:**
  - `pyproject.toml` — package definition, dependencies, entry point
  - `indexer/__init__.py`
  - `indexer/cli.py` — click group + stub subcommands
  - `indexer/db.py` — stub
  - `indexer/parser.py` — stub
  - `indexer/mapper.py` — stub
  - `indexer/enricher.py` — stub
  - `indexer/query.py` — stub
  - `indexer/migrations/.gitkeep`
  - `tests/test_cli_smoke.py` — smoke test

---

### T2: Database init, migration runner, and `index init` command ⭐ critical path
- **Size:** M (2 points)
- **Type:** Task
- **Depends on:** T1
- **Acceptance Criteria:**
  - `indexer/migrations/001_initial.sql` contains the full DDL: `nodes`, `edges`, `files`, `nodes_fts` (FTS5), `index_meta` tables, and all indexes
  - `db.py` exposes `bootstrap(db_path)`: creates `.codeindex/` directory if absent, creates DB, runs all `NNN_*.sql` migrations in numeric order, sets `index_meta.schema_version` to highest applied migration number
  - `db.py` exposes `get_connection(db_path) -> sqlite3.Connection` with WAL mode and `PRAGMA foreign_keys = ON` enabled
  - `index init` runs `bootstrap()`, exits 0 on success (including no-op if schema is already current)
  - `index init` runs pending migrations if schema version is stale (upgrade scenario)
  - `index init` exits 2 with `[ERROR] Schema version mismatch (DB: X, code: Y). Run: index reset --yes && index build` on downgrade scenario
  - On first run, `index init` checks `.gitignore` and appends `.codeindex/` if absent, printing `[SETUP] Added .codeindex/ to .gitignore` to stderr
  - DB path resolution follows 4-step order: `--db` → `CODEINDEX_DB` env var → `.codeindex/codeindex.db` → exit 2
  - Unit tests verify: fresh create, no-op on re-run, migration upgrade, downgrade detection, gitignore append
- **Technical Notes:**
  - Migration runner: use `__file__`-relative path — NOT CWD-relative. `pathlib.Path(__file__).parent / "migrations"` locates migrations correctly regardless of where `index` is invoked from. CWD-relative `glob.glob('indexer/migrations/*.sql')` silently finds nothing when invoked outside the project root.
  - WAL mode: `PRAGMA journal_mode=WAL` — improves concurrent read performance
  - `PRAGMA foreign_keys = ON` must be set on every new connection (SQLite doesn't persist this)
  - The `nodes_fts` FTS5 table uses `content=nodes` (external content table) — DDL in `001_initial.sql` must match the schema exactly
  - See: `tech-spec-code-indexing.md` § Data Model, § Migration Strategy, § Database Path Resolution
- **Files to Create/Modify:**
  - `indexer/migrations/001_initial.sql` — full DDL
  - `indexer/db.py` — `bootstrap()`, `get_connection()`, `resolve_db_path()`
  - `indexer/cli.py` — wire `index init` to `db.bootstrap()`
  - `tests/conftest.py` — shared `db_conn` pytest fixture: calls `db.bootstrap(':memory:')`, yields connection, closes it. All downstream test modules use this fixture to avoid duplicating DB setup and filesystem coupling.
  - `tests/test_db.py` — unit tests for all bootstrap/migration scenarios

---

### T3a: Phase 1 — Python AST parser, incremental detection, and cAST chunking ⭐ critical path
- **Size:** M (2 points)
- **Type:** Story
- **Depends on:** T2
- **Acceptance Criteria:**
  - `parser.py` extracts nodes from Python files using `ast` (stdlib): `file`, `class`, `function`, `method` node types with `name`, `qualified_name`, `signature`, `docstring`, `start_line`, `end_line`, `raw_source`, `content_hash`
  - `content_hash` is SHA-256 of `raw_source`
  - `files` table is upserted with `content_hash`, `last_modified`, `language`, `node_count`
  - Changed files are detected by comparing `content_hash` in `files` table — only changed/new files are re-parsed (incremental build)
  - Files matching `.gitignore` patterns and `*.db` inside `.codeindex/` are excluded
  - cAST chunking: functions/methods whose estimated token count exceeds `--token-limit` (default 512) are split into syntactically complete subtrees; parent-child relationship preserved via `qualified_name` hierarchy
  - When upserting a node with a changed `content_hash`, `enriched_at` is cleared to NULL (ensures re-enrichment on next `index enrich` run)
  - Syntax error in a file: log `[WARNING] Skipped: {path} — {error}` to stderr; continue; exit code carries warning flag
  - Unit tests: Python class + method extraction, incremental skip on unchanged file, cAST chunking of oversized function, `.gitignore` exclusion, syntax error handling, `enriched_at` cleared on content change
- **Technical Notes:**
  - Token estimate: `len(raw_source.split()) * 1.3` sufficient for MVP
  - `qualified_name` format: `ClassName.method_name` for methods, `function_name` for top-level, file path for file nodes
  - Node ID format: `{file_path}::{node_type}::{qualified_name}` — use file path relative to repo root for stability
  - `parse_file()` and `parse_directory()` interfaces defined here; T3b will extend `parse_file()` for non-Python languages
  - See: `tech-spec-code-indexing.md` § Architecture (Phase 1), § Data Model (nodes table)
- **Files to Create/Modify:**
  - `indexer/parser.py` — `parse_file()`, `parse_directory()`, `chunk_node()` (cAST)
  - `tests/test_parser.py` — unit tests with fixture source files
  - `tests/fixtures/sample.py` — Python fixture with class, methods, and a large function

---

### T3b: Phase 1 — tree-sitter integration for Kotlin and TypeScript ⭐ critical path
- **Size:** M (2 points)
- **Type:** Story
- **Depends on:** T3a
- **Acceptance Criteria:**
  - `parser.parse_file()` handles Kotlin files using `tree-sitter` with `tree-sitter-kotlin` grammar: extracts `class`, `function`, `method`, `interface`, `object` nodes
  - `parser.parse_file()` handles TypeScript files using `tree-sitter` with `tree-sitter-typescript` grammar: extracts `class`, `function`, `method`, `interface` nodes
  - Node fields are identical to Python nodes: `id`, `qualified_name`, `signature`, `docstring`, `start_line`, `end_line`, `raw_source`, `content_hash`
  - cAST chunking (implemented in T3a) applies to tree-sitter nodes using the same token estimate
  - Java and Go are explicitly deferred — stub language detection logs `[WARNING] Unsupported language: {lang}, skipping` and continues
  - Unit tests: Kotlin class + method extraction, TypeScript class + function extraction, unsupported language warning
- **Technical Notes:**
  - Use `tree_sitter.Language` + `tree_sitter.Parser` with appropriate grammar bindings
  - Grammar installation: `tree-sitter-kotlin` and `tree-sitter-typescript` must be declared in `pyproject.toml`
  - tree-sitter grammars compile to native binaries on first use — first install will take longer; document in README
  - See: `tech-spec-code-indexing.md` § Architecture (Phase 1), § Implementation Stack
- **Files to Create/Modify:**
  - `indexer/parser.py` — extend `parse_file()` with tree-sitter dispatch
  - `pyproject.toml` — add `tree-sitter`, `tree-sitter-kotlin`, `tree-sitter-typescript`
  - `tests/test_parser.py` — extend with Kotlin + TypeScript test cases
  - `tests/fixtures/Sample.kt` — Kotlin fixture
  - `tests/fixtures/sample.ts` — TypeScript fixture

---

### T4: Phase 2 — GrepRAG dependency mapper and FTS5 rebuild ⭐ critical path
- **Size:** M (2 points)
- **Type:** Story
- **Depends on:** T3b
- **Acceptance Criteria:**
  - `mapper.py` runs ripgrep against source files for each changed node's exported identifiers; resolves matches to node IDs by file path + line range lookup in `nodes` table
  - Directed edges are written to `edges` table with correct `edge_type`: `calls`, `imports`, `inherits`, `overrides`, `references`, `instantiates`
  - Edge deletion is scoped correctly: outbound edges from changed nodes are deleted before re-mapping; dangling inbound edges (to deleted/renamed nodes) are also purged; inbound edges from unchanged nodes are retained
  - Callers of changed nodes have their outbound edges re-resolved (step 3e in spec)
  - `nodes_fts` virtual table is rebuilt unconditionally at end of Phase 2 (`INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')`)
  - If `ripgrep` is not found on `PATH`, exits 2 with clear error message and installation hint
  - Unit tests cover: edge insertion, correct outbound-only deletion, dangling inbound purge, FTS5 rebuild runs, ripgrep not-found error
- **Technical Notes:**
  - Ripgrep invocation: `subprocess.run(["rg", "--json", "-n", identifier, repo_root])` — parse JSON output for file/line matches
  - Import resolution: parse `import` statements extracted during Phase 1 (stored in `raw_source` or as a separate field); resolve module path to file path using `sys.path`-style resolution
  - FTS5 rebuild: `conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")` — this is the correct SQLite FTS5 external-content rebuild syntax
  - See: `tech-spec-code-indexing.md` § Architecture (Phase 2), § Phase-Boundary Rebuild (steps 3a–3f)
- **Files to Create/Modify:**
  - `indexer/mapper.py` — `map_dependencies()`, `delete_outbound_edges()`, `rebuild_fts()`
  - `tests/test_mapper.py` — unit tests

---

### T5: `index build` command with lock file and exit codes ⭐ critical path
- **Size:** S (1 point)
- **Type:** Story
- **Depends on:** T4
- **Acceptance Criteria:**
  - `index build` auto-bootstraps the DB if it does not yet exist (calls `db.bootstrap()` before Phase 1)
  - `index build` acquires a lock file at `.codeindex/build.lock` before starting; releases on completion or error; if lock is older than 10 minutes at startup, treats as stale, removes it, and prints `[WARNING] Stale lock file removed` to stderr
  - `index build` runs Phase 1 (`parser.parse_directory()`) then Phase 2 (`mapper.map_dependencies()`) in sequence
  - `--phase PREPARE|DEPLOY` tag is written to `index_meta.last_phase_boundary`
  - Exit codes: 0 (all phases complete, no warnings), 1 (parse warnings or files skipped), 2 (fatal: ripgrep not found, DB locked, schema mismatch)
  - All progress messages go to stderr; nothing written to stdout during build
  - `index_meta` is updated with `last_full_build`, `total_nodes`, `total_edges` on completion
  - Integration test: run `index build` against a small fixture repository; verify DB contains expected nodes and edges
- **Technical Notes:**
  - Lock file: write PID + timestamp to `.codeindex/build.lock` on acquire; `os.remove()` in a `finally` block
  - Exit code propagation: collect warnings during Phase 1+2; if any, exit 1 after updating `index_meta`
  - See: `tech-spec-code-indexing.md` § Phase-Boundary Rebuild (Build Execution), § CLI Interface (Exit Codes, Output Streams)
- **Files to Create/Modify:**
  - `indexer/cli.py` — wire `index build` to parser + mapper, lock file logic, exit code handling
  - `tests/test_build.py` — integration test with fixture repo

---

### T6: `index enrich` command — Phase 3 LLM enrichment ⭐ critical path
- **Size:** M (2 points)
- **Type:** Story
- **Depends on:** T5
- **Acceptance Criteria:**
  - `index enrich` selects nodes where `enriched_at IS NULL` — this is sufficient because T3a clears `enriched_at` to NULL whenever `content_hash` changes, ensuring updated nodes are automatically re-queued for enrichment
  - Before starting, prints estimate to stderr: `X nodes to enrich. Estimated time: ~Y minutes.` (Y = X / 60, rounded)
  - For each node: builds context (signature + docstring + parent + children + callers + callees), calls Claude API with enrichment prompt, parses JSON response, stores `semantic_summary`, `domain_tags`, `inferred_responsibility`, `enriched_at`, `enrichment_model`
  - Retry with exponential backoff: 3 attempts on rate limit or timeout; on final failure, node remains unenriched, warning logged to stderr
  - Malformed JSON response: log warning, skip node, continue
  - `--dry-run`: exit after printing estimate (step 2), exit code 0, no API calls made
  - `--model MODEL`: overrides default `claude-sonnet-4-6`; stored in `index_meta.enrichment_model`
  - After all nodes processed, rebuilds `nodes_fts` for enriched nodes
  - Updates `index_meta.unenriched_nodes` count
  - Exit 0 if all nodes enriched; exit 1 if any nodes remain unenriched
  - Unit tests: hash-gating (already-enriched node is skipped), dry-run exits without API calls, malformed JSON handled, retry logic
- **Technical Notes:**
  - Claude API: use `anthropic` Python SDK; API key via `ANTHROPIC_API_KEY` env var (not stored in DB)
  - Enrichment prompt template: exactly as specified in `tech-spec-code-indexing.md` § Enrichment Prompt
  - Exponential backoff: `time.sleep(2 ** attempt)` for attempts 0, 1, 2 (waits 1s, 2s, 4s)
  - FTS5 partial update after enrich: re-insert enriched node rows into FTS5 via `DELETE FROM nodes_fts WHERE rowid = ?` then `INSERT INTO nodes_fts ...`
  - See: `tech-spec-code-indexing.md` § `index enrich` Execution, § Enrichment Prompt, § Performance Considerations
- **Files to Create/Modify:**
  - `indexer/enricher.py` — `enrich_nodes()`, `build_node_context()`, `call_llm()`, `parse_enrichment_response()`
  - `indexer/cli.py` — wire `index enrich` command
  - `pyproject.toml` — add `anthropic` dependency
  - `tests/test_enricher.py` — unit tests (mock Claude API)

---

### T7: `index query` command — router, lexical, graph, semantic paths
- **Size:** M (2 points)
- **Type:** Story
- **Depends on:** T5
- **Acceptance Criteria:**
  - `index query "<input>"` routes to lexical, graph, or semantic search based on query router logic (identifier-like → lexical, natural language → semantic, `--type` override)
  - Lexical search: ripgrep exact match → node lookup by file+line → re-rank by specificity → return top-k `NodeResult`
  - Graph search: recursive SQLite edge traversal up to `--depth` (default 2); returns `GraphResult` with nodes + edges
  - Semantic search: FTS5 BM25 query against `nodes_fts`; returns top-k `NodeResult` ranked by score
  - Fallback: if lexical returns empty, falls back to semantic; if semantic returns empty, falls back to lexical
  - `--with-source` includes `raw_source` in results; omitted by default
  - `--format text|json|jsonl`: text output to stdout for TTY, json/jsonl for pipeline consumption; format auto-detected as `text` when stdout is a TTY (`sys.stdout.isatty()`)
  - All query results written to **stdout**; progress/debug messages to **stderr**
  - Returns exit 0 with results, exit 1 if index not found or unenriched and semantic search requested, exit 2 on fatal DB error
  - Unit tests: lexical match found, semantic FTS5 query, graph traversal 2 hops, jsonl output format, fallback routing
- **Technical Notes:**
  - `NodeResult`, `GraphResult`, `EdgeResult` dataclasses defined in `query.py` as per spec
  - Graph traversal: use recursive CTE in SQLite for clean multi-hop traversal (`WITH RECURSIVE ...`)
  - JSON output: `json.dumps([dataclasses.asdict(r) for r in results])`; jsonl: one `json.dumps()` per line
  - See: `tech-spec-code-indexing.md` § Query Interface
- **Files to Create/Modify:**
  - `indexer/query.py` — `QueryRouter`, `lexical_search()`, `graph_search()`, `semantic_search()`, result dataclasses
  - `indexer/cli.py` — wire `index query` command
  - `tests/test_query.py` — unit tests

---

### T8: `index status` and `index reset` commands
- **Size:** S (1 point)
- **Type:** Task
- **Depends on:** T5
- **Acceptance Criteria:**
  - `index status` prints to stdout: node count, edge count, unenriched node count, last build timestamp, last phase boundary, schema version, DB path
  - `index status` exits 1 with a helpful message if DB does not exist (`index build has not been run yet`)
  - `index reset` drops and recreates the database schema (runs `bootstrap()` fresh)
  - `index reset` requires `--yes`/`-y` in non-interactive use; without it in a non-TTY context, exits 2 with message: `Destructive operation requires --yes flag`
  - `index reset` in interactive TTY prompts for confirmation: `This will delete all indexed data. Continue? [y/N]`
  - Schema version mismatch detected by `index status`: prints `[WARNING] Schema version mismatch` with instructions
  - Unit tests: status output format, reset requires `--yes`, reset no-op prompt path
- **Technical Notes:**
  - Non-TTY detection: `not sys.stdin.isatty()` → require `--yes`
  - `index reset` calls `db.bootstrap()` after dropping all tables — reuse the same bootstrap logic from T2
  - See: `tech-spec-code-indexing.md` § CLI Interface (Commands, Exit Codes)
- **Files to Create/Modify:**
  - `indexer/cli.py` — wire `index status` and `index reset`
  - `tests/test_status_reset.py` — unit tests

---

## Size Summary

| Size | Count | Story Points | Estimated Days Each |
|------|-------|--------------|-------------------|
| S    | 3     | 1 each       | < 0.5 day         |
| M    | 6     | 2 each       | 0.5–1 day         |

**Total story points:** 15
**Total estimated effort:** 6–8 days (sequential) / 5–6 days (T7 and T8 parallel after T5)

---

## Implementation Order (sequential execution)

1. **T1** — Scaffold — no dependencies
2. **T2** — DB init + `index init` — foundation for all data
3. **T3a** — Phase 1 Python parser + cAST + incremental detection
4. **T3b** — Phase 1 tree-sitter (Kotlin + TypeScript) — extends T3a
5. **T4** — Phase 2 mapper — edges + FTS5
6. **T5** — `index build` — wires Phase 1 + 2, adds lock file + exit codes
7. **T6** — `index enrich` — Phase 3, depends on nodes + edges existing
8. **T7** — `index query` — depends on DB having data
9. **T8** — `index status` + `index reset` — depends on DB existing

---

## Notes for the Software Engineer

- This is a **Python CLI tool**, not a Kotlin/Spring service. Ignore Spring/JPA patterns.
- All phases are **synchronous** — no async/await, no threading required for MVP.
- The DB is **never committed** to source control. `.codeindex/` is always in `.gitignore`. The `index init` command handles this automatically on first run.
- **FTS5 external content table**: `nodes_fts` uses `content=nodes`. This means FTS5 does not store the content itself — it only stores the index. You must trigger a manual rebuild (`INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')`) whenever the `nodes` table changes. This is already specified in T4 (after Phase 2) and T6 (after enrichment).
- **Exit codes matter**: 0 = clean, 1 = completed with warnings (orchestrator logs and continues), 2 = fatal (orchestrator blocks and alerts). Get this right in T5 — it affects pipeline reliability.
- **stdout is sacred**: only structured query results go to stdout. Everything else (phase banners, warnings, progress, setup notices) goes to stderr. This is enforced from T1 so don't use `print()` for progress — use `click.echo(..., err=True)`.
- Token counting in T3 is intentionally lightweight — a rough estimate is sufficient for the MVP. Exact tokenization can be added later if chunking quality is inadequate.
