# Peer Review: Implementation Plan & Tickets — Hybrid Code Indexing System

**Phase:** Architecture (Implementation Plan + Jira Tickets)
**Reviewed:** March 25, 2026
**Artifacts reviewed:**
- `implementation-plan-code-indexing.md`
- `tickets-code-indexing.md`
- `tech-spec-code-indexing.md` (for consistency check)

---

## Verdict: 🔄 REVISE

One must-fix item (migration runner path resolution will silently break when invoked outside the project root) and four should-fix items. The overall plan structure is sound — dependency ordering is correct, tasks are well-scoped, and ticket quality is high. Address PR-001 before implementation begins.

---

## Automated Checklist

### Implementation Plan — Structure

- [x] **Task ordering** — T1 → T2 → T3 → T4 → T5 → {T6, T7, T8}. No task depends on an unfinished later task.
- [x] **Valid DAG** — Linear chain with fork after T5. No cycles.
- [x] **Critical path identified** — Marked with ⭐ throughout the plan. Fork at T5 is explicitly noted.
- [x] **Granularity** — All tasks S or M. 4×S (1pt), 4×M (2pt). No L tasks, no XL.

### Implementation Plan — Task Quality

- [x] **Acceptance criteria** — Every task has specific, testable acceptance criteria verifiable by running tests.
- [x] **Context sufficiency** — Each task references specific tech spec sections. Technical notes include concrete function signatures and SQL syntax.
- [x] **Files identified** — All tasks list files to create/modify.
- [x] **Size estimates** — All tasks sized. Distribution appropriate (50% S, 50% M).

### Jira Tickets — Completeness

- [x] **Tickets match plan** — 1:1 mapping: T1–T8 → WIO-T1 through WIO-T8. No gaps.
- [x] **WIO project** — All tickets assigned to WIO project.
- [x] **Dependencies linked** — "Blocked by" and "Blocks" present on every ticket. Dependency summary table at bottom of tickets file.
- [x] **Acceptance criteria present** — All AC from plan reproduced faithfully in tickets.
- [x] **Technical notes present** — Concrete implementation hints, code examples, library calls, SQL snippets.
- [x] **Labels applied** — `feature-code-indexing` on all tickets.
- [x] **Story points set** — Match plan: S=1, M=2.

### Consistency Checks

- [x] **Spec ↔ Plan alignment** — Every tech spec section is covered by at least one task. `index init`, migration strategy, lock file, FTS5 rebuild, exit codes, stdout/stderr split, DB path resolution all have corresponding tasks.
- [x] **Plan ↔ Tickets alignment** — Acceptance criteria, technical notes, and file lists are consistent between plan and tickets.
- [-] **Event catalog consistency** — Event storming was not run; not applicable.
- [x] **Naming consistency** — `nodes_fts`, `.codeindex/`, `codeindex.db`, `index build/enrich/query/status/reset/init`, `content_hash`, `index_meta` are consistent across spec, plan, and tickets.

**Checklist Summary:** 17/17 passed, 0 failed, 1 not applicable.

---

## Deep Review Findings

### 🔴 Must Fix

**[PR-001] Migration runner uses CWD-relative path — will silently fail outside project root**
- **Location:** `implementation-plan-code-indexing.md` § T2 Technical Notes; `tickets-code-indexing.md` § WIO-T2 Technical Notes
- **Issue:** The migration runner is specified as `glob.glob('indexer/migrations/*.sql')`. This path is relative to the current working directory at runtime. When `index init` or `index build` is invoked from `/home/user/projects/myapp/` and the `indexer` package is installed via `pip install -e .` from that same directory, this happens to work. But when invoked from any other directory (e.g. `cd /tmp && index init --db /home/user/projects/myapp/.codeindex/codeindex.db`), `glob.glob('indexer/migrations/*.sql')` finds nothing — no migrations are run, `schema_version` is never set, and subsequent operations behave unpredictably.
- **Impact:** This is a hard runtime failure that's invisible in development (where the developer always runs from the project root) but will reliably break in CI, Docker, and pipeline orchestrator contexts where the working directory is not the package root. It will also confuse any operator using `--db` with an explicit path from a different directory.
- **Suggestion:** Use a `__file__`-relative path to locate the migrations directory, not a CWD-relative one:
  ```python
  import pathlib
  MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"
  migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
  ```
  Update both the plan and the ticket. This is a one-line fix but must be specified correctly before the engineer implements T2, as it affects the core architecture of the migration runner.

---

### 🟡 Should Fix

**[PR-002] T3 is undersized — multi-language tree-sitter + cAST chunking is M-to-L work**
- **Location:** `implementation-plan-code-indexing.md` § T3; `tickets-code-indexing.md` § WIO-T3
- **Issue:** T3 is sized M (< 1 day) but covers: Python `ast` parsing, tree-sitter integration for Kotlin + TypeScript (minimum), cAST split/merge algorithm, incremental change detection, `.gitignore` exclusion, and 5 unit test scenarios including fixture files for multiple languages. tree-sitter grammar binding setup alone can take half a day the first time (grammar compilation, binary dependencies, API differences between grammar versions). cAST chunking is a non-trivial recursive algorithm.
- **Impact:** If the engineer uses this size estimate for sprint planning, they will miss the task. This is the highest-risk task in the pipeline — an overrun here delays everything downstream.
- **Suggestion:** Split T3 into two tasks:
  - **T3a (M):** Python `ast` parsing, incremental change detection, `.gitignore` exclusion, cAST chunking. Delivers a working parser for Python files with all infrastructure in place.
  - **T3b (M):** tree-sitter integration for Kotlin and TypeScript. Depends on T3a (reuses the same parser interface). Java and Go remain stretch goals.
  This keeps each task within M size while making the tree-sitter risk isolatable.

---

**[PR-003] T7 is undersized — full query router + 3 search paths + formatting is M work**
- **Location:** `implementation-plan-code-indexing.md` § T7; `tickets-code-indexing.md` § WIO-T7
- **Issue:** T7 is sized S (< 0.5 day) but covers: query routing logic, lexical search with ripgrep + re-ranking, graph search with recursive CTE, semantic FTS5 search, fallback routing between all three, result dataclasses (`NodeResult`, `GraphResult`, `EdgeResult`), `--format` TTY detection, stdout/stderr separation, and 6 unit test scenarios. The recursive CTE alone needs careful testing. S tasks should be "< 100 lines of production code" by the spec's own sizing guide — this is comfortably 200+ lines.
- **Impact:** Underestimation here will cause a mid-sprint surprise. The query interface is also the primary consumer-facing API — rushing it increases the risk of subtle bugs in result formatting or routing logic.
- **Suggestion:** Resize T7 to M (2 points). Alternatively, split into T7a (router + lexical search, S) and T7b (graph search + semantic search + formatting, M) — but a single M is cleaner given T7 already has clear acceptance criteria.

---

**[PR-004] Hash-gating criterion in T6 is ambiguous — no `enrichment_content_hash` column exists**
- **Location:** `implementation-plan-code-indexing.md` § T6 Acceptance Criteria; `tickets-code-indexing.md` § WIO-T6 Acceptance Criteria
- **Issue:** The criterion reads: "`enricher.enrich_nodes(conn, model, dry_run)` selects nodes where `enriched_at IS NULL` or `content_hash` differs from stored enrichment hash." The phrase "stored enrichment hash" has no referent in the schema — the `nodes` table has a single `content_hash` column that is updated by Phase 1 whenever `raw_source` changes. There is no separate column recording what the `content_hash` was at enrichment time. An engineer implementing this literally will have to invent a solution (most likely: check `enriched_at IS NULL`, which misses the re-enrichment case for updated nodes).
- **Impact:** If implemented as "only enrich when `enriched_at IS NULL`", updated-but-previously-enriched nodes will never be re-enriched after a code change. Semantic summaries will go stale silently.
- **Suggestion:** Either (a) add an `enrichment_content_hash TEXT` column to the `nodes` table schema (updated alongside `enriched_at`) and update T6's criterion to `WHERE enriched_at IS NULL OR content_hash != enrichment_content_hash` — or (b) clarify that Phase 1 clears `enriched_at` to NULL whenever it updates a node's `content_hash`, making "select where `enriched_at IS NULL`" sufficient. Option (b) requires adding a note to T3's acceptance criteria: "When upserting a node with a changed `content_hash`, clear `enriched_at` to NULL." Either path is fine — pick one and make it explicit before T3 and T6 are implemented.

---

**[PR-005] No shared test fixture (`conftest.py`) specified — every test module will reinvent DB setup**
- **Location:** `implementation-plan-code-indexing.md` § T2 Files to Create; `tickets-code-indexing.md` § WIO-T2 Files to create/modify
- **Issue:** Tests for T2 through T8 all need a SQLite database. The plan doesn't mention a shared `tests/conftest.py` with a `db_conn` fixture that creates an in-memory DB and runs migrations. Without it, each test module (test_db, test_parser, test_mapper, test_enricher, test_query, test_status_reset) will independently manage DB setup — either duplicating bootstrap logic or coupling to the filesystem. This is exactly the kind of test infrastructure debt that causes test brittleness.
- **Impact:** Duplicated setup code across 6+ test modules; tests that couple to filesystem paths instead of in-memory DBs; harder to run tests in parallel. Fixing this later requires touching every test file.
- **Suggestion:** Add `tests/conftest.py` to T2's file list with this note: "Create a `db_conn` pytest fixture that (1) calls `db.bootstrap(':memory:')` to create an in-memory SQLite DB, (2) yields the connection, (3) closes it. All subsequent test modules import and use this fixture. This ensures test isolation and avoids filesystem coupling." One line added to T2 prevents debt across all downstream tickets.

---

### 🟢 Looks Good

- **Dependency ordering is correct and well-reasoned.** Scaffolding before schema, schema before data, data before orchestration, orchestration before queries. The fork at T5 (T7 and T8 independent of T6) correctly captures that query functionality doesn't depend on LLM enrichment.

- **T5 is appropriately minimal.** It's easy to let "wiring" tickets bloat by absorbing concerns from adjacent tasks. T5 stays focused: auto-bootstrap, lock file, exit codes, `index_meta` update. Everything else stays in its own ticket.

- **Technical notes are genuinely useful.** The level of specificity is exactly right — exact function signatures, SQL syntax for the FTS5 rebuild, `open(..., 'x')` for lock file exclusivity, `WITH RECURSIVE` CTE skeleton. An engineer can implement from these notes without re-reading the full spec. This is rare in implementation plans and worth preserving.

- **"Out of Scope" sections prevent scope creep.** Every ticket explicitly names what's deferred to which future ticket. This prevents an engineer from "while I'm in here" additions that inflate scope and break the dependency graph.

- **"Notes for the Software Engineer" section is excellent.** Calling out that this is Python (not Kotlin/Spring), explaining FTS5 external content behaviour, and reinforcing the stdout/stderr contract up front will prevent the most common misunderstandings. The note about token counting being intentionally lightweight is especially good — it resets expectations without apology.

- **T2 is identified as the highest-dependency task.** Everything downstream blocks on the schema. Sizing it M (not S) correctly reflects this, and the acceptance criteria cover all the edge cases: fresh create, no-op, upgrade, downgrade, gitignore append. This level of coverage in T2 will make T3–T8 much smoother.

---

## Consistency Check

- **Spec ↔ Plan alignment:** All spec sections covered. `index init`, migration strategy, lock file behaviour, phase-boundary rebuild, FTS5 unconditional rebuild, exit codes, stdout/stderr separation, DB path resolution, `.gitignore` auto-update, `--no-gitignore-update`, and `--dry-run` on enrich are all traceable to specific task acceptance criteria.
- **Plan ↔ Tickets alignment:** 1:1 mapping, consistent AC and technical notes throughout. The only minor discrepancy is that the plan's T4 file list mentions `delete_outbound_edges()` and `rebuild_fts()`, while the ticket adds `purge_dangling_edges()` as a third function. The ticket is more complete — no action needed, but if the plan is used as a standalone reference it could be updated to match.
- **Naming consistency:** Consistent across all three artifacts.

---

## Summary

The plan and tickets are well-structured with one real blocker: the migration runner's CWD-relative path (PR-001) will silently fail in any non-project-root execution context. This is a one-line fix in both the plan and the ticket but must be corrected before T2 is implemented. The four should-fix items — T3 undersize, T7 undersize, hash-gating ambiguity, and missing conftest — are all straightforward and will prevent the most likely implementation surprises. Address PR-001 as a must, and at minimum PR-004 (hash-gating) before the engineer reaches T3/T6, to avoid a costly rework of the schema mid-implementation.

---

## ⏸️ Awaiting Human Sign-Off

Review complete. Verdict: **🔄 REVISE** — 1 must-fix, 4 should-fix.

Please confirm how to proceed:
- **Fix first** — apply the 5 findings to the plan and tickets before implementation begins
- **Approve with conditions** — fix PR-001 and PR-004 only; proceed with the rest as implementation notes
- **Override** — proceed to implementation accepting the findings as engineer judgment calls
- **Add feedback** — you have additional observations
