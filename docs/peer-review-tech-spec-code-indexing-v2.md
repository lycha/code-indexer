# Peer Review: Tech Spec — Hybrid Code Indexing System (Pass 2)

**Phase:** Architecture (Tech Spec only — implementation plan and Jira tickets not yet submitted)
**Reviewed:** March 25, 2026
**Artifacts reviewed:**
- `tech-spec-code-indexing.md` (revised — all 8 PR findings from Pass 1 applied)
- `02-prd.md` (PRD coverage verification)
- `peer-review-tech-spec-code-indexing.md` (Pass 1 findings — for regression check)

---

## Verdict: ✅ APPROVED

All three must-fix items from Pass 1 are correctly resolved. All five should-fix items are correctly resolved. Four minor new observations are noted below as should-fix items; none block approval. The spec is architecturally sound, complete, and ready to proceed to the implementation plan phase.

---

## Automated Checklist

### Tech Spec — Completeness

- [x] **PRD coverage** — FR-16 (index available to BUILD/DEPLOY), FR-17 (refreshed before DEPLOY), and OQ-06 (build and maintenance) all addressed. FR-16 extended to PREPARE by design — well-reasoned and consistent with Pass 1.
- [x] **Context & goals** — Overview clearly states problem, solution, and scope. Motivation (token cost, hallucination risk, agent coherence) is grounded in cited research.
- [x] **Architecture approach** — Three-phase pipeline rationale is explained. Hybrid AST + GrepRAG + LLM model is well-justified. SQLite over vector DB is explicitly reasoned.
- [x] **Module placement** — Component table maps responsibilities to Python modules. CLI utility placement is justified.

### Tech Spec — API Design

- [-] **Endpoint definitions** — Not applicable. CLI tool, no HTTP API.
- [x] **Error handling** — Exit codes 0/1/2 now fully specified. Error table covers six failure modes with concrete behaviours. ✅ Resolves PR-001.
- [x] **Consistency** — stdout/stderr separation is now documented and exemplified. `--format` flag standardises query output. ✅ Resolves PR-004. CLI conventions are internally consistent.
- [-] **Idempotency** — Not applicable to a build tool.
- [x] **Validation rules** — `--type`, `--phase`, `--format` constraints are specified. `--yes` requirement for reset is specified.

### Tech Spec — Data Model

- [x] **Entity definitions** — All tables fully defined: `nodes`, `edges`, `files`, `nodes_fts`, `index_meta`.
- [-] **Aggregate boundaries** — DDD not applicable.
- [x] **Migration strategy** — Fresh create on first run, `schema_version` gating, `reset` escape hatch.
- [x] **Index strategy** — Comprehensive. Partial index on `enriched_at IS NULL` for unenriched node scans, composite indexes on `(source_id, edge_type)` and `(target_id, edge_type)`.
- [x] **Data integrity** — `ON DELETE CASCADE`, `CHECK` constraints on `node_type` and `edge_type`, `NOT NULL` on required fields.

### Tech Spec — Integration Points

- [x] **External services** — Claude API (retry with exponential backoff, 3 attempts) and ripgrep subprocess are documented with failure modes.
- [-] **Async flows** — No async processing. All phases are synchronous.
- [x] **Failure handling** — Six error scenarios documented with concrete behaviours. Continue-on-partial-failure for LLM enrichment is explicitly justified.
- [-] **Eventual consistency** — Not applicable; index is rebuilt from source on each build.

### Tech Spec — Non-Functional Requirements

- [x] **Quantified targets** — Phase 1+2 speed targets given (60 seconds for 500 files, 10 seconds incremental). Phase 3 timing now documented: 8–10 minutes worst-case first run, under 60 seconds for small incremental. ✅ Resolves Pass 1 checklist gap.
- [-] **Security** — Local CLI tool. Claude API key via environment variable is implied (not explicitly stated, but acceptable given scope).
- [x] **Caching** — Hash-gated enrichment is explicitly justified as persistent memoisation. Decision is documented.

### Tech Spec — Open Questions

- [x] **No blocking unknowns** — All four open questions marked non-blocking with reasonable notes.
- [x] **Risk assessment** — Pass 1 gap partially addressed: FTS5 staleness risk is resolved by design. Tree-sitter grammar availability is surfaced in OQ-I-01. A minor gap remains: no explicit mention of the risk that tree-sitter grammar availability varies by language version (e.g. Kotlin grammar may lag behind language releases), but this is OQ-I-01 scope.

**Checklist Summary:** 17/20 passed, 0 failed, 6 not applicable. (Pass 1: 14/20 passed, 4 failed.)

---

## Pass 1 Regression Check

Verifying all 8 findings from Pass 1 are correctly resolved:

| Finding | Status | Notes |
|---------|--------|-------|
| PR-001 — CLI exit codes | ✅ Resolved | Exit Codes table (0/1/2) present in CLI Interface section |
| PR-002 — DB path resolution | ✅ Resolved | 4-step resolution order documented; `.codeindex/` subdirectory; `--db` as global option |
| PR-003 — FTS5 staleness after non-enriched builds | ✅ Resolved | Step 3f unconditionally rebuilds FTS5 at end of Phase 2 |
| PR-004 — Query output format | ✅ Resolved | `--format text\|json\|jsonl` on query; stdout/stderr separation documented with examples |
| PR-005 — reset `--yes` flag | ✅ Resolved | `--yes, -y` option defined; "required for non-interactive / scripted use" |
| PR-006 — Edge deletion scope | ✅ Resolved | Step 3a scopes to outbound only; dangling inbound edges for deleted/renamed nodes handled separately; step 3e re-resolves affected callers |
| PR-007 — enrich command redundancy | ✅ Resolved | `--enrich` removed from `build`; `index enrich` is the standalone command; two-step workflow documented in examples |
| PR-008 — DB exclusion / .gitignore | ✅ Resolved | Database File Exclusions section added; auto-.gitignore with stderr notice; `--no-gitignore-update` escape hatch |

---

## Deep Review Findings

### 🔴 Must Fix

_No must-fix findings._

---

### 🟡 Should Fix

**[PR2-001] `index build --model MODEL` affects a different command — the handoff mechanism is unspecified**
- **Location:** CLI Interface section — `Options (build)`, `--model MODEL`
- **Issue:** The `--model` option on `build` is described as "Override enrichment model used by subsequent `enrich` run." Since `build` no longer runs enrichment (PR-007 fix), this option has no direct effect on `build` itself — it only persists a model preference somewhere for `enrich` to pick up. The spec doesn't say where. The implied mechanism is writing to `index_meta.enrichment_model`, but this handoff is not stated. A developer implementing this will have to guess.
- **Impact:** Minor implementation ambiguity. No impact on architecture correctness, but will cause a question during implementation.
- **Suggestion:** Either (a) remove `--model` from `build` entirely (put it only on `enrich` where it takes effect), or (b) add a note: "Writes `enrichment_model` to `index_meta` for use by the next `index enrich` run." Option (a) is cleaner — it follows the single-responsibility principle the PR-007 fix was aiming for.

---

**[PR2-002] `--rebuild` flag referenced in Migration Strategy but not defined in CLI Commands**
- **Location:** Data Model section — Migration Strategy; CLI Interface section — Commands
- **Issue:** Migration Strategy says "the CLI prints a warning and offers `--rebuild` to drop and recreate." The Commands section defines no `--rebuild` flag. The `reset` command is the correct mechanism for this, but the migration warning copy suggests a flag that doesn't exist. This creates a confusing user experience when the user follows the warning's instruction and finds no such flag.
- **Impact:** Minor documentation inconsistency that will confuse the operator when they hit a schema version mismatch.
- **Suggestion:** Update the Migration Strategy prose to read: "...and instructs the operator to run `index reset && index build` to recover." Remove the `--rebuild` reference. The `reset` command already covers this.

---

**[PR2-003] `--dry-run` behaviour on `index enrich` is unspecified within the execution block**
- **Location:** `index enrich` Execution section
- **Issue:** The `[--dry-run]` option appears in the command signature and in `Options (enrich)` ("Show how many nodes would be enriched; make no API calls"), but the execution block (steps 1–6) doesn't specify where `--dry-run` causes the command to exit. Specifically: does it run step 1 (count unenriched nodes), print the step 2 estimate, then exit? Or does it also print which nodes would be enriched?
- **Impact:** Low. The intent is clear enough ("make no API calls"), but the implementer will make a judgment call that may not match operator expectations.
- **Suggestion:** Add a branch to the execution block: "If `--dry-run`: exit after step 2 with exit code 0. No API calls made." Optionally add: "With `--verbose`, also list qualified names of nodes to be enriched."

---

**[PR2-004] Lock file location and crash recovery behaviour are unspecified**
- **Location:** Error Handling section — "SQLite locked (concurrent access)" row
- **Issue:** The error table mentions "Only one index build runs at a time (lock file guard)" but doesn't specify: (1) where the lock file lives, (2) what name it has, (3) how stale locks are detected and cleaned up after a crash. A stale lock file after a `SIGKILL` will permanently block subsequent builds until manually deleted, with no clear guidance.
- **Impact:** Low probability in single-operator use, but guaranteed to happen at some point (power loss, force-quit). The operator will be confused about why `index build` hangs silently.
- **Suggestion:** Add one sentence to the spec: "Lock file: `.codeindex/build.lock`. On startup, if the lock file is older than 10 minutes, it is treated as stale and removed automatically with a warning to stderr." This covers the common crash-recovery case without requiring operator intervention.

---

### 🟢 Looks Good

- **All three must-fix items from Pass 1 are cleanly resolved.** The FTS5 unconditional rebuild (PR-003) and edge deletion scoping (PR-006) in particular required non-trivial reasoning and are implemented correctly. The two-step `build` + `enrich` split is a better CLI design than the original `--enrich` flag.

- **The stdout/stderr separation documentation is excellent.** The example showing exactly which messages go to which stream (including the empty stdout during build) is the right level of specificity. Any developer or operator reading this will know exactly what to expect.

- **The 4-step DB path resolution order is well-designed.** Explicit priority order with a clear error message on fallthrough (`exit 2` with actionable text) is correct. The `.codeindex/` subdirectory decision is justified and makes `.gitignore` management genuinely simpler.

- **Phase 3 timing documentation is operator-friendly.** "8–10 minutes" on first run, with the `enrich` command printing an estimate before starting, is exactly the right way to handle a slow operation. Operators who know what to expect won't kill the process prematurely.

- **`--no-gitignore-update` escape hatch is a thoughtful addition.** CI-server use case (shared read-only index committed to repo) is niche but real. Documenting the escape hatch without making it prominent is the right balance.

- **The `NodeResult.raw_source: str | None` opt-in design holds up.** Token budgeting enforced at the interface level. Agents can't accidentally blow context window on a graph traversal.

---

## Consistency Check

- **Spec ↔ System Design (Section 10) alignment:** Consistent. Phase-boundary rebuild model, three-phase architecture, SQLite choice, and `.codeindex/` placement all align.
- **Naming consistency:** `nodes`, `edges`, `files`, `index_meta`, `nodes_fts` naming is consistent throughout. `cAST`, `GrepRAG`, `LLM Enrichment` terminology is consistent. `index build` / `index enrich` split is internally consistent.
- **PRD alignment:** FR-16, FR-17, OQ-06 are directly addressed. No PRD requirements silently omitted.
- **Implementation plan / Jira tickets:** Not submitted as part of this review. Required before the architecture phase gate is complete.

---

## Summary

The revised spec passes with no must-fix findings. All eight Pass 1 items are resolved correctly, and the fixes are coherent — the PR-007 `enrich` command separation, in particular, produced a cleaner CLI design than the original. Four minor should-fix items are noted: the `--model` handoff ambiguity (PR2-001), a `--rebuild` reference that no longer matches the command surface (PR2-002), the unspecified `--dry-run` exit point in the `enrich` execution block (PR2-003), and the unspecified lock file location and stale lock handling (PR2-004). These are all one-liner fixes in the spec and do not affect the architecture. Address them before the implementation plan is written to give the implementer complete guidance.

The spec is ready to proceed to the implementation plan and Jira tickets.

---

## ⏸️ Awaiting Human Sign-Off

Review complete. Verdict: **✅ APPROVED** — 0 must-fix items, 4 should-fix items (one-liners, non-blocking).

Please confirm how to proceed:
- **Approve** — proceed to implementation plan and Jira tickets
- **Fix first** — apply the 4 should-fix items to the tech spec before moving on
- **Override** — proceed to implementation ignoring should-fix items
- **Add feedback** — you have additional input to include
