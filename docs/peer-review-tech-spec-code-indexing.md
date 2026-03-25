# Peer Review: Tech Spec — Hybrid Code Indexing System

**Phase:** Architecture (Tech Spec only — no implementation plan or Jira tickets submitted)
**Reviewed:** March 25, 2026
**Artifacts reviewed:**
- `tech-spec-code-indexing.md`
- `02-prd.md` (for PRD coverage check)
- `03-system-design.md` Section 10 (for consistency check)

---

## Verdict: 🔄 REVISE

The spec is well-grounded, clearly written, and makes sound architectural choices. The data model and query interface are the strongest sections. However there are three must-fix items: the CLI design has gaps that violate standard CLI tool conventions (exit codes, stderr/stdout separation, `--help` contract), the `codeindex.db` placement strategy has a correctness problem for multi-project setups, and the FTS5 virtual table is missing its rebuild trigger after incremental node updates. These are all fixable without rethinking the approach.

---

## Automated Checklist

### Tech Spec — Completeness

- [x] **PRD coverage** — FR-16 (index available to BUILD/DEPLOY), FR-17 (refreshed before DEPLOY), and OQ-06 (build and maintenance) are all explicitly addressed. FR-16 notes the index is also available to PREPARE, which is an intentional and well-reasoned extension of the PRD.
- [x] **Context & goals** — Problem and solution are stated clearly in the Overview. The motivation (token cost, hallucination risk, agent coherence) is grounded in research cited in `03-system-design.md`.
- [x] **Architecture approach** — Three-phase pipeline rationale is explained. The hybrid AST + GrepRAG + LLM model is well-justified. The choice of SQLite over a vector DB is explicitly reasoned.
- [x] **Module placement** — Component table clearly maps responsibilities to Python modules (`indexer/parser.py`, `indexer/mapper.py`, etc.). The tool stands alone as a CLI utility, which is a justified placement given the pipeline-boundary invocation model.

### Tech Spec — API Design

- [-] **Endpoint definitions** — Not applicable. This is a CLI tool with no HTTP API surface.
- [-] **Idempotency** — Not applicable to a CLI build tool in this form.
- [ ] ⚠️ **Error handling** — The error table covers build-time failures well, but there is no specification of CLI exit codes. A tool that exits with code 0 when enrichment partially fails (unenriched nodes) will silently mislead the pipeline orchestrator. Standard practice: exit 0 = success, exit 1 = partial failure (with warning), exit 2 = fatal failure. This is unspecified.
- [ ] ⚠️ **Consistency** — The CLI design mixes stdout and stderr responsibilities without specifying which output goes where. Progress logs, warnings, and result summaries should be stderr; structured query output (JSON/text for agent consumption) should be stdout. This distinction is missing.
- [-] **Validation rules** — Not applicable in the HTTP sense. CLI input validation is partially addressed via the `--type` and `--phase` constraints.

### Tech Spec — Data Model

- [x] **Entity definitions** — All tables are fully defined: `nodes`, `edges`, `files`, `nodes_fts`, `index_meta`. Field types, constraints, and comments are thorough.
- [-] **Aggregate boundaries** — DDD not applicable here; this is an infrastructure tool.
- [x] **Migration strategy** — Documented: fresh create on first run, `schema_version` gating, `--rebuild` escape hatch. Appropriate for a fully derived store.
- [x] **Index strategy** — Comprehensive. Partial index on `enriched_at IS NULL` for unenriched node scans is a nice touch. Composite indexes on `(source_id, edge_type)` and `(target_id, edge_type)` cover the graph traversal patterns well.
- [x] **Data integrity** — `ON DELETE CASCADE` on edges, `CHECK` constraints on `node_type` and `edge_type`, `NOT NULL` on required fields. Well done.

### Tech Spec — Integration Points

- [x] **External services** — Claude API dependency is documented with retry strategy (3 attempts, exponential backoff). ripgrep subprocess dependency is documented with failure mode.
- [-] **Async flows** — No async processing in the design. All phases are synchronous.
- [x] **Failure handling** — Six error scenarios are documented with concrete behaviours. The "continue on partial failure" approach for LLM failures is explicitly reasoned (unenriched count surfaced in `status`).
- [-] **Eventual consistency** — Not applicable; the index is rebuilt from source of truth on each build.

### Tech Spec — Non-Functional Requirements

- [ ] ⚠️ **Quantified targets** — Phase 1+2 speed targets are given ("under 60 seconds for 500 files") but there is no target for Phase 3 enrichment time, which is the slow path and the one most likely to surprise the operator. A 500-node first-time enrichment at ~1 API call/second could take 8–10 minutes. This should be documented so the operator isn't alarmed.
- [-] **Security** — This is a local CLI tool operating on a local repository. The Claude API key handling deserves a mention (environment variable, not flag argument), but this is minor.
- [x] **Caching** — No caching layer needed; the hash-gated enrichment is itself a form of persistent memoisation. The decision is implicitly justified by the architecture.

### Tech Spec — Open Questions

- [x] **No blocking unknowns** — All four open questions are marked non-blocking and have reasonable notes. OQ-I-02 (enrich at PREPARE vs DEPLOY) is the most consequential and is well-framed.
- [ ] ⚠️ **Risk assessment** — No explicit risk section. Two risks are worth naming: (1) tree-sitter grammar availability and maintenance for the target language set; (2) the FTS5 virtual table requiring a manual rebuild step after incremental updates, which is easy to forget and silently produces stale semantic search results.

**Checklist Summary:** 14/20 passed, 4 failed, 6 not applicable.

---

## Deep Review Findings

### 🔴 Must Fix

**[PR-001] CLI exit codes are unspecified — orchestrator cannot detect failure**
- **Location:** CLI Interface section
- **Issue:** The spec defines five commands but specifies no exit code contract. When the pipeline orchestrator calls `index build --enrich --phase PREPARE` as a subprocess, it will check the exit code to determine whether to proceed. Currently, partial LLM failures ("node remains unenriched; build continues") would exit 0, making them indistinguishable from a fully successful build.
- **Impact:** The pipeline orchestrator could advance a card to the architect agent with an incomplete semantic index, silently degrading agent context quality. The operator has no way to know enrichment was incomplete from the exit code alone.
- **Suggestion:** Add an explicit exit code contract to the spec:
  ```
  Exit codes:
    0  — All phases completed successfully (or skipped cleanly)
    1  — Completed with warnings (e.g. unenriched nodes, skipped files)
    2  — Fatal failure (ripgrep not found, SQLite locked after retries,
           schema version mismatch)
  ```
  The orchestrator can then decide: treat exit 1 as proceed-with-warning, treat exit 2 as block-and-alert-operator.

---

**[PR-002] `codeindex.db` co-location strategy is underspecified and fragile for multi-project use**
- **Location:** Data Model section ("co-located with project root"); CLI Interface section (no `--db` option)
- **Issue:** The spec states the database is "co-located with the project root" but provides no mechanism to specify the path. The AI OS will potentially run against multiple project repositories from the same installation. If `codeindex.db` is always written to the current working directory (or a hardcoded relative path), running `index build` from the wrong directory silently creates a new empty database rather than updating the correct one.
- **Impact:** An operator running `index build` from their home directory or from the AI OS install directory creates a phantom database and the agents query a stale or empty index without any error.
- **Suggestion:** Add a `--db PATH` option (or `CODEINDEX_DB` environment variable) with a clear resolution order:
  ```
  DB resolution order:
    1. --db PATH argument
    2. CODEINDEX_DB environment variable
    3. .codeindex/codeindex.db in current working directory
    4. Error if none of the above
  ```
  This also makes the tool easier to test and integrate into CI-style invocations.

---

**[PR-003] FTS5 virtual table is only rebuilt at the end of Phase 3 — stale after incremental Phase 1/2 builds without `--enrich`**
- **Location:** Phase-Boundary Rebuild section, step 4c ("Rebuild nodes_fts virtual table")
- **Issue:** The spec rebuilds `nodes_fts` only at the end of Phase 3. When the operator runs `index build` without `--enrich` (Phase 1 + 2 only), the FTS5 table is never refreshed. Nodes that were renamed, moved, or deleted in Phase 1 remain indexed in FTS5, and new nodes are absent. Any subsequent semantic query against the stale FTS5 table returns wrong results with no error.
- **Impact:** An architect agent querying semantically in PREPARE gets results referencing deleted or renamed nodes. The index appears healthy (`index status` shows no issues) but serves incorrect data.
- **Suggestion:** Rebuild `nodes_fts` at the end of Phase 2, unconditionally, not just at the end of Phase 3:
  ```
  Step 3 (end): Rebuild nodes_fts from nodes table
    → DELETE FROM nodes_fts WHERE ...
    → INSERT INTO nodes_fts SELECT id, qualified_name, semantic_summary,
        domain_tags, inferred_responsibility FROM nodes
  Step 4c: (FTS rebuild already done; Phase 3 only updates enrichment columns,
            which are part of the FTS content — trigger a targeted update for
            enriched nodes only)
  ```

---

### 🟡 Should Fix

**[PR-004] `query` command output format is unspecified — makes programmatic consumption by agents unreliable**
- **Location:** CLI Interface section — `query` command
- **Issue:** The spec shows interactive example queries but doesn't define the output format. When an agent calls `index query "validateCartState" --type lexical --with-source`, does it receive JSON, a custom text format, a table? The `NodeResult` dataclass is defined in the Query Interface section but there's no mapping between the dataclass and what the CLI actually emits.
- **Impact:** The agent runner spec (currently out of scope) will need to parse this output. An informal text format will require fragile parsing logic and will break across spec versions. This is worth settling now.
- **Suggestion:** Specify the default output format explicitly and add a `--format` flag:
  ```
  --format FORMAT     Output format: text (default) | json | jsonl
  ```
  `jsonl` (one JSON object per line) is ideal for streaming results to an agent runner that processes matches incrementally. Make `json` the default when `--format` is not specified for non-interactive use (detectable via `sys.stdout.isatty()`).

---

**[PR-005] No `--dry-run` option on `reset` — a destructive command with only an interactive confirmation guard**
- **Location:** CLI Interface section — `reset` command
- **Issue:** `reset` drops and recreates the database, which is irreversible (though the data is re-derivable). The spec mentions "confirms before executing" but this interactive prompt breaks non-interactive use (CI, scripted pipelines, the orchestrator itself). Passing `--yes` or `--force` to skip confirmation is a well-established convention (e.g. `terraform destroy -auto-approve`, `docker system prune -f`).
- **Suggestion:** Add `--yes` / `-y` flag to skip interactive confirmation for scripted use. Document that without `--yes`, reset always prompts on stderr.

---

**[PR-006] Phase 2 edge deletion scope is too broad — deletes edges for all nodes in changed files, including unchanged nodes**
- **Location:** Phase-Boundary Rebuild section, step 3a ("Delete all edges for nodes in changed files")
- **Issue:** The spec deletes all edges for nodes in changed files, then re-resolves only edges originating from changed nodes. If `CartService.kt` changes, all edges where `CartService.*` is the source are deleted and re-resolved — correct. But all edges where `CartService.*` is the *target* (i.e. other nodes that call into CartService) are also deleted and not re-resolved. This leaves inbound edges absent until the next full build.
- **Impact:** Graph queries for "what calls CartService.applyDiscount?" return empty results after an incremental build, even though those callers haven't changed.
- **Suggestion:** Refine the deletion scope:
  ```
  Step 3a: Delete edges WHERE source_id IN (nodes from changed files)
           -- Outbound edges from changed nodes: safe to delete and re-resolve
           -- Inbound edges to changed nodes: retain (the callers haven't changed)
           -- BUT: if a changed node was renamed/deleted, stale inbound edges remain
  ```
  The cleaner solution: delete ALL edges touching changed nodes (both source and target), then re-resolve outbound edges from changed nodes AND re-resolve outbound edges from all nodes that previously pointed INTO changed nodes. Add this nuance as a note in the spec.

---

**[PR-007] `enrich` command is redundant with `build --enrich` and creates two ways to do the same thing**
- **Location:** CLI Interface section — `enrich` command
- **Issue:** The spec defines a standalone `enrich` command that runs Phase 3 on unenriched nodes, and also a `--enrich` flag on `build`. These overlap significantly. Having two invocation paths for the same operation increases documentation surface, user confusion, and implementation maintenance.
- **Impact:** Minor, but a CLI that has `index enrich` and `index build --enrich` as near-synonyms will confuse users about which to use and when.
- **Suggestion:** Keep `enrich` as the standalone command for "run enrichment pass only, without re-parsing." Remove `--enrich` from `build` and instead document the intended workflow:
  ```
  # Full build with enrichment (two steps, intentional)
  index build
  index enrich

  # Or combined via shell
  index build && index enrich
  ```
  This makes the phases explicit and composable, which is more aligned with Unix conventions than a flag that silently chains operations.

---

**[PR-008] No mention of `.gitignore` / exclusion of the database file itself from indexing**
- **Location:** Phase-Boundary Rebuild section, step 1
- **Issue:** The spec notes that the build respects `.gitignore` patterns, but `codeindex.db` will be co-located at the project root. If it's not explicitly excluded, Phase 1 will attempt to parse it as a source file, fail with a parse error, log a warning, and continue. Harmless but noisy. More importantly, `codeindex.db` should be added to `.gitignore` by default — committing a binary SQLite file into the project repo is rarely intentional.
- **Suggestion:** Add to the spec: "The build process always excludes `codeindex.db` and `*.db` files from parsing. The CLI should add `codeindex.db` to `.gitignore` on first run if not already present (with operator confirmation)."

---

### 🟢 Looks Good

- **The FTS5 virtual table choice is excellent.** Using SQLite's built-in FTS5 for semantic search keeps the zero-infrastructure promise intact while delivering BM25-ranked full-text search. Many specs would reach for Elasticsearch or a vector DB here. This is the right call for the scale.

- **Hash-gated enrichment is well-designed.** Storing `content_hash` on the node and only re-enriching when it changes is an elegant solution to the cost problem. The fact that this works correctly across full rebuilds AND incremental builds without any special-casing is a sign of clean design.

- **The enrichment prompt is strong.** Including immediate neighbours (parent, children, callers, callees) in the LLM context rather than just the node source in isolation is the correct approach — it produces semantically accurate summaries. This mirrors the hierarchical summarisation methodology from the research basis.

- **The error handling table is specific and concrete.** Six failure modes documented with exact behaviour, not vague "handle gracefully" language. The decision to continue on LLM partial failure (rather than abort the whole build) is the right call and is clearly justified.

- **The `NodeResult` dataclass with `raw_source: str | None` is a good API contract.** Making raw source opt-in (only included when `--with-source` is passed) enforces the token-budgeting principle at the interface level. Agents can't accidentally blow their context window by forgetting a flag.

- **The query fallback logic is pragmatic.** Lexical-first → fallback to semantic is the right default. It matches the Repoformer finding that 80% of agent queries are exact identifier lookups, while still gracefully handling natural language queries without requiring the caller to know query type upfront.

---

## Consistency Check

- **Spec ↔ System Design (Section 10) alignment:** The tech spec is consistent with Section 10 of the system design. The phase-boundary rebuild model, three-phase architecture, and SQLite choice all match. The tech spec correctly adds detail not present in the system design (FTS5 virtual table, exit codes gap, `enrich` command) without contradicting it.
- **Naming consistency:** `nodes`, `edges`, `files`, `index_meta` naming is consistent between the system design schema sketch and the tech spec schema. `cAST`, `GrepRAG`, and `LLM Enrichment` terminology is consistent throughout.
- **PRD alignment:** FR-16, FR-17, and OQ-06 are directly addressed. No PRD requirements are silently omitted.
- **Implementation plan / Jira tickets:** Not submitted as part of this review. These are required before the architecture phase can be considered complete.

---

## Summary

The spec is architecturally sound, well-motivated, and clearly written. The three must-fix items are all correctness issues rather than design flaws: missing exit codes (PR-001), underspecified database path resolution (PR-002), and a stale FTS5 table after non-enriched builds (PR-003). None require rethinking the architecture — they are specification gaps. The should-fix items are primarily CLI convention gaps: output format contract (PR-004), destructive command safety (PR-005), edge deletion scope correctness (PR-006), command redundancy (PR-007), and database exclusion from indexing (PR-008). Address PR-001 through PR-003 as blockers. PR-004 through PR-008 are strongly recommended before the implementation plan is written, since they affect how the CLI modules are designed.

Note: an implementation plan and Jira tickets were not included in this review. These are required to complete the architecture phase gate.

---

## ⏸️ Awaiting Human Sign-Off

Review complete. Verdict: **🔄 REVISE** — 3 must-fix items, 5 should-fix items.

Please confirm how to proceed:
- **Approve** — proceed to implementation plan despite findings
- **Override** — proceed to implementation despite findings
- **Send back** — update the tech spec to address findings first
- **Add feedback** — you have additional input to include
