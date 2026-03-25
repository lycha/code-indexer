# System Design: AI Engineering Operating System

**Author:** Kris
**Date:** March 24, 2026
**Status:** Draft
**Version:** 0.1

---

## 1. System Overview

The AI Engineering Operating System is a pipeline of artifact transformations, controlled by a human operator via a Kanban interface. Each ticket is a git-backed document tree that accumulates structured Markdown artifacts as it passes through agent-owned columns.

```
┌─────────────────────────────────────────────────────────────────┐
│                        HUMAN OPERATOR                           │
│  Creates tickets │ Moves cards │ Edits artifacts │ Approves     │
└────────────────────────────┬────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │   KANBAN BOARD  │
                    │  Phase Groups   │
                    │  + Columns      │
                    └────────┬────────┘
                             │
          ┌──────────────────▼──────────────────┐
          │           COLUMN EXECUTION           │
          │                                      │
          │  Worker Agent → Artifact             │
          │       ↓                              │
          │  Reviewer Agent → Findings           │
          │       ↓                              │
          │  Sign-off / Escalate / Rework        │
          └──────────────────┬──────────────────┘
                             │
          ┌──────────────────▼──────────────────┐
          │         ARTIFACT STORE               │
          │   Git-backed Markdown document tree  │
          │   Linear history, structured commits │
          └──────────────────┬──────────────────┘
                             │
          ┌──────────────────▼──────────────────┐
          │         CONTEXT LAYER                │
          │  Codebase Index │ CONSTRAINTS.md     │
          │  Ticket history │ Phase-scoped       │
          └─────────────────────────────────────┘
```

---

## 2. Pipeline Architecture

### 2.1 Phase Groups and Columns

The pipeline is organised into four phase groups. Each group contains columns. The column map below is a working draft — columns marked `[TBD]` are placeholders pending finalisation.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  PLAN                  │  PREPARE               │  BUILD        │  DEPLOY    │
├────────────────────────┼────────────────────────┼───────────────┼────────────┤
│  Backlog               │  Architecture Spike    │  Implement    │  QA        │
│  (human triage)        │  (architect agent)     │  (eng agent)  │  (qa agent)│
│                        │                        │               │            │
│  Product Scoping       │  Tech Spec             │  Code Review  │  DoD Gate  │
│  (pm agent)            │  (architect agent)     │  (review agent│  (human)   │
│                        │                        │               │            │
│  PRD Review            │  Spec Review           │               │  Release   │
│  (reviewer agent)      │  (reviewer agent)      │               │  [TBD]     │
└────────────────────────┴────────────────────────┴───────────────┴────────────┘
```

**Notes:**
- Backlog is a human-only stage — no agent runs here. Cards sit until the operator moves them forward.
- Review columns may be merged into the preceding column as a sign-off state rather than a separate column (open design question OQ-01).
- DEPLOY columns are the least defined — needs further design.

### 2.2 Artifact Produced Per Column

| Column | Input | Output Artifact |
|--------|-------|-----------------|
| Backlog | Operator input | `ticket.md` |
| Product Scoping | `ticket.md` | `plan/prd.md` |
| PRD Review | `plan/prd.md` | `plan/prd-review.md` |
| Architecture Spike | `plan/prd.md`, `plan/prd-review.md` | `prepare/spike.md` |
| Tech Spec | `prepare/spike.md` | `prepare/tech-spec.md` |
| Spec Review | `prepare/tech-spec.md` | `prepare/spec-review.md` |
| Implementation | All PREPARE artifacts | `build/implementation-notes.md` + code commits |
| Code Review | Code + `build/implementation-notes.md` | `build/code-review.md` |
| QA | All BUILD artifacts | `deploy/qa-report.md` |
| DoD Gate | All artifacts + `ticket.md` DoD | `deploy/dod-verification.md` |

---

## 3. Ticket and Artifact Model

### 3.1 Ticket Structure

Tickets are created by the operator. The ticket itself is immutable after creation — only the artifact tree grows.

```markdown
# Ticket: {ticket-id}

## Title
{title}

## Description
{description}

## Definition of Done
{acceptance criteria — evaluated only at DoD Gate}

## Notes
{additional context, links, constraints}
```

### 3.2 Artifact Tree Structure

```
/tickets/
  {ticket-id}/
    ticket.md               ← immutable after creation
    plan/
      prd.md
      prd-review.md
    prepare/
      spike.md
      tech-spec.md
      spec-review.md
    build/
      implementation-notes.md
      code-review.md
    deploy/
      qa-report.md
      dod-verification.md
```

### 3.3 Git Commit Convention

Every agent action and human edit produces a git commit with a structured message:

```
[ARTIFACT][vN][AGENT][action: reason]

Examples:
[PRD][v1][pm-agent][create]
[PRD][v2][pm-agent][rework: architect-feedback-prd-assumptions-wrong]
[PRD][v2][human][edit: clarified-target-user]
[TECH-SPEC][v1][architect-agent][create]
[CODE-REVIEW][v1][reviewer-agent][create: WARNING×3 INFO×1]
```

**Rules:**
- History is always linear — no branches
- One agent owns an artifact at a time
- Human edits are committed directly and attributed as `[human]`
- Version number increments on each rework cycle

---

## 4. Agent Model

### 4.1 Agent Spec

All agents — worker and reviewer — share the same spec structure:

```yaml
name: pm-agent
role: worker                    # worker | reviewer
system_prompt: |
  You are a product manager...
context_scope:
  - ticket
  - business_context
  - product_decisions
input_spec:
  - ticket.md
output_spec:
  - plan/prd.md
model: claude-opus-4-6
self_verification: true         # produces plan + checklist before executing
```

### 4.2 Agent Library (Draft Roster)

| Agent | Role | Phase | Primary Skill |
|-------|------|-------|---------------|
| `pm-agent` | worker | PLAN | Translates business context into structured PRD |
| `prd-reviewer` | reviewer | PLAN | Reviews PRD against business goals rubric |
| `architect-agent` | worker | PREPARE | Spikes technical options; produces tech spec |
| `spec-reviewer` | reviewer | PREPARE | Reviews tech spec against architectural constraints |
| `engineer-agent` | worker | BUILD | Implements code against spec and constraints |
| `code-reviewer` | reviewer | BUILD | Reviews code against quality and constraint rubric |
| `qa-agent` | worker | DEPLOY | Writes and runs automated tests |
| `dod-reviewer` | reviewer | DEPLOY | Evaluates final artifact against ticket DoD |

> ⚠️ This roster is a first draft. Agent names, boundaries, and responsibilities will evolve during build.

### 4.3 Self-Verification Protocol

When `self_verification: true`, the worker agent must:

1. **Plan** — produce a numbered checklist of steps it will take to complete the task
2. **Execute** — complete each step, ticking items off the checklist
3. **Verify** — review its output against the checklist and confirm each item is satisfied
4. **Produce** — output the artifact only after self-verification passes

The checklist is included in the artifact commit as a separate section or file, making the agent's reasoning visible.

### 4.4 Review Cycle

```
Worker produces artifact
  │
  ▼
Reviewer evaluates against column rubric
  │
  ├─ No findings → sign-off flag set → card eligible to advance
  │
  ├─ INFO findings → sign-off flag set (INFO does not block)
  │
  ├─ WARNING findings
  │     iterations < max → worker reworks
  │     iterations = max → escalate to human OR mark done (per column config)
  │
  └─ BLOCKER findings → always escalate to human (regardless of iteration count)
```

**Finding format:**

```markdown
## Review Findings

### BLOCKER
- [BLOCKER] No authentication check on POST /api/tickets endpoint

### WARNING
- [WARNING] Missing error handling for git commit failure
- [WARNING] Context scope includes full codebase — should be index only

### INFO
- [INFO] Consider extracting ticket validation into a separate function
```

---

## 5. Context Layer

### 5.1 Context Scoping by Phase

| Context Object | PLAN | PREPARE | BUILD | DEPLOY |
|----------------|------|---------|-------|--------|
| `ticket.md` | ✓ | ✓ | ✓ | ✓ |
| Prior phase artifacts | — | PLAN only | PLAN + PREPARE | All |
| Business context docs | ✓ | — | — | — |
| `CONSTRAINTS.md` | — | ✓ | ✓ | ✓ |
| Codebase index (high-level) | — | ✓ | ✓ | ✓ |
| Codebase index (full) | — | — | ✓ | ✓ |
| Reviewer findings (current column) | — | ✓ | ✓ | ✓ |

### 5.2 Codebase Index

The codebase index is a structured file tree where each node has a description of the file's purpose, ownership, and dependencies. Agents use the index to navigate to relevant files rather than receiving the raw codebase.

```
src/
  services/
    ticketService.ts    ← "Handles ticket CRUD and state transitions. Depends on ticketRepo, eventBus."
    agentRunner.ts      ← "Orchestrates agent execution for a given column. Emits AgentStarted, AgentCompleted."
  repos/
    ticketRepo.ts       ← "PostgreSQL persistence for tickets and artifact metadata."
```

- PREPARE agents receive the high-level index (file names + descriptions, no file content)
- BUILD agents receive the full index and can request individual file content
- The index is refreshed before the DEPLOY phase begins

> See **Section 10** for the full design of the Hybrid AST + GrepRAG Code Indexing System that implements and populates this index.

### 5.3 CONSTRAINTS.md

A board-level configuration file containing architectural laws injected into all PREPARE and BUILD agents.

```markdown
# Project Constraints

## Architecture
- Repository pattern for all data access — no direct DB calls outside repos
- All service methods must be covered by unit tests
- Event-driven communication between services via internal event bus

## Code Standards
- Kotlin + Spring Boot
- No raw SQL — use JPA/QueryDSL
- All endpoints require authentication middleware

## Security
- No secrets in code — use environment variables
- Input validation on all public endpoints
```

---

## 6. Human Operator Model

### 6.1 Operator Actions

| Action | Trigger | Effect |
|--------|---------|--------|
| Create ticket | Manual | New ticket + `ticket.md` committed to artifact store |
| Move card forward | Manual | Next column's worker agent begins execution |
| Move card backward | Manual | Target column enters rework state; agent receives full history |
| Direct edit | Manual | Artifact modified; new commit attributed to `[human]` |
| Pause agent | Manual | Agent execution halted; card stays in current column |
| Approve BLOCKER | Manual | BLOCKER dismissed; agent may rework or card may advance |
| Override sign-off | Manual | Card advances even without sign-off flag |
| Set advance mode | Config | Per column: `auto` or `manual` |

### 6.2 Advance Mode

- **Auto:** When sign-off flag is set (and no unresolved BLOCKERs), card automatically moves to next column
- **Manual:** Sign-off flag is a notification to operator; operator must explicitly move the card

Recommended default: `manual` for all PLAN and PREPARE columns; `auto` may be appropriate for lower-risk BUILD sub-steps once the operator has built confidence in agent quality.

### 6.3 Ticket Sizing Discipline

The pipeline is designed around small, focused tickets. This is not a stylistic preference — it is a structural requirement that the system depends on.

**Why small tickets matter for this pipeline:**

The codebase index is rebuilt at phase boundaries, not on every commit. A ticket that spans weeks of implementation will accumulate a large body of code changes between the PREPARE index snapshot and the DEPLOY snapshot. This does not break the pipeline, but it means PREPARE-phase agents (architect, spec writer) are reasoning from a slightly older codebase view. For small tickets the delta is negligible; for large tickets it compounds.

Agent context windows are also a practical constraint. Large tickets produce large artifact trees. As a card moves leftward for rework or spans multiple review cycles, the accumulated context history passed to agents grows. Small tickets stay manageable.

**Guidance for operators:**

- A ticket should represent a single coherent change — one feature slice, one bug fix, one refactor target
- If a ticket requires changes across more than three to four files, consider splitting it
- If a ticket's Definition of Done has more than five acceptance criteria, it is likely too large
- The pipeline has no mechanism to enforce ticket size — this is an operator discipline enforced by habit and review

**What "small" means in practice:**

| Ticket type | Reasonable scope |
|-------------|-----------------|
| Feature | One user-facing capability, end-to-end through one service |
| Bug fix | One root cause, one fix location |
| Refactor | One class or one responsibility boundary |
| Infrastructure | One configuration change or one dependency upgrade |

---

## 7. Open Design Decisions

| # | Decision | Options | Recommendation |
|---|----------|---------|----------------|
| OD-01 | Are review stages separate columns or sign-off states within a column? | Separate columns (more visible) vs. sub-states (cleaner board) | Sub-states preferred — reduces board width; review is part of the column lifecycle |
| OD-02 | How do ticket DoD and column rubric coexist in reviewer prompt? | Inject both; inject only rubric; separate reviewer per concern | Inject only column rubric for all columns except DoD Gate; DoD Gate reviewer gets only the ticket DoD |
| OD-03 | Operator interface | Custom web UI, existing Kanban tool (Trello/Linear), CLI | TBD — CLI first for MVP speed; web UI for v1.1 |
| OD-04 | Agent handoff context | Full artifact history injected; structured handoff doc; shared memory | Full artifact history in v1 — simpler, no memory layer needed |
| OD-05 | Codebase index implementation | Build custom; use Shotgun; use existing tools | Evaluate Shotgun as a dependency; build lightweight custom indexer if integration is complex |

---

## 8. Known Hard Problems

These are problems that are acknowledged but deliberately deferred:

- **Index staleness:** ~~Resolved in Section 10.~~ The index is rebuilt at phase boundaries (before PREPARE, before DEPLOY) rather than tracked in real-time. This is intentional: the index is an agent context tool, not a live mirror. The model depends on tickets being small — operators should not accumulate large bodies of change within a single card (see Section 6.3).
- **DoD vs. rubric coexistence:** How the reviewer agent handles both the column rubric and the ticket DoD in a single pass without conflation is unresolved. OD-02 above is the current best hypothesis.
- **Agent quality variance:** Reviewer agents may find issues on every pass, creating perpetual WARNING loops. The max iteration + escalation model mitigates this but doesn't eliminate it. Agent prompt quality is the primary lever.
- **Leftward movement with rich history:** When a card moves back two or more columns, the receiving agent's context window can become very large. Context pruning strategies will be needed as artifact trees grow.

---

## 9. Glossary

| Term | Definition |
|------|------------|
| Ticket | The unit of work. Created by the operator. Contains title, description, DoD, and notes. |
| Artifact | A Markdown file produced by an agent as the output of a column. |
| Artifact Tree | The collection of all artifacts attached to a ticket, stored as a git-backed folder structure. |
| Column | A stage in the pipeline. Has a configured worker agent, reviewer agent, rubric, and advance rules. |
| Phase Group | A logical grouping of columns: PLAN, PREPARE, BUILD, DEPLOY. |
| Agent | A spec defining an LLM's role, context scope, input/output contract, and model. |
| Agent Library | The curated collection of agent specs maintained by the operator. |
| Rubric | Review criteria for a specific column. Defines what "correct execution" means for that stage. |
| DoD | Definition of Done. End-to-end acceptance criteria on the ticket. Evaluated only at the DoD Gate. |
| BLOCKER | A reviewer finding that always escalates to the human operator, regardless of iteration count. |
| Sign-off Flag | Set by the reviewer agent when a column's output meets the rubric. Enables card advancement. |
| Advance Mode | Per-column setting: `auto` (card moves on sign-off) or `manual` (operator must move card). |
| CONSTRAINTS.md | Board-level architectural laws injected into PREPARE and BUILD agents. |
| Codebase Index | A structured, description-annotated file tree of the project, used for token-efficient context. |
| AST | Abstract Syntax Tree. A hierarchical, typed representation of source code structure. |
| cAST | Chunking via Abstract Syntax Trees. Structure-aware code chunking methodology. |
| GrepRAG | Index-free lexical retrieval framework using grep-style exact identifier matching. |
| Semantic Node | An index node enriched with LLM-generated natural language metadata at build time. |

---

## 10. Hybrid Code Indexing System

### 10.1 Scientific Context and Motivation

The design of the codebase index is grounded in peer-reviewed research on repository-level code generation and token optimisation for LLM agents. This section documents the scientific basis for the architectural choices made.

#### The Context Window Bottleneck

As LLMs are applied to real-world software engineering tasks, they encounter a fundamental architectural constraint: the context window acts analogously to RAM in a traditional operating system. Feeding a large, multi-file codebase directly into an LLM's context window introduces computational and financial inefficiencies at scale. Critically, simply expanding the context window does not resolve the problem — as context length increases, LLMs suffer from "context pollution," where irrelevant or redundant data dilutes the attention mechanism and degrades performance, including hallucination of non-existent APIs and failure to locate information buried mid-prompt.

> *"Just as a traditional operating system must meticulously curate which processes and data streams reside in physical RAM to prevent system degradation, an LLM agent must be supplied with highly curated, mathematically optimised context."*
> — Repository-Level Code Indexing and Token Optimization for LLM Agents (2026)

#### Problem with Naive Text Chunking

Traditional Retrieval-Augmented Generation (RAG) pipelines divide documents into fixed-size, line-based segments. This approach is profoundly destructive when applied to source code, which possesses rigid syntactic boundaries, logical hierarchies, and strict scoping rules. Fixed-size chunking will frequently split a function definition from its body, separate a class method from its variables, or isolate a return statement from the logic that triggered it. When an LLM retrieves such a syntactically fragmented chunk, it loses structural context and hallucinates incorrect assumptions about variable scopes, parameter types, and return values.

**Reference:** cAST: Enhancing Code Retrieval-Augmented Generation with Structural Chunking via Abstract Syntax Tree (arXiv:2506.15655v1; ACL Anthology 2025.findings-emnlp.430)

#### The Case for AST-Based Chunking (cAST)

The **cAST** methodology — Chunking via Abstract Syntax Trees — overhauls the indexing pipeline by parsing source code into complete ASTs and applying a recursive split-then-merge process. Rather than severing code at arbitrary token counts, cAST breaks down AST root nodes into progressively smaller, yet syntactically complete, subtrees. A merging phase recombines sibling nodes within token embedding limits, ensuring every chunk is a self-contained, semantically coherent unit devoid of dangling syntactical fragments.

Empirical results on rigorous benchmarks demonstrate the superiority of this approach over fixed-size chunking:

| Benchmark | Metric | Improvement over Baseline |
|-----------|--------|--------------------------|
| RepoEval | Recall@5 | +4.3 points |
| SWE-bench | Pass@1 | +2.67 points |

Industrial validation from Databricks' Knowledge Assistant project confirmed that failing to index code via its syntactic components severely degraded retrieval scores on complex, cross-file reasoning tasks.

**Reference:** cAST (arXiv:2506.15655v1); CAST: Enhancing Code Summarization with Hierarchical Splitting and Reconstruction of Abstract Syntax Trees (ACL Anthology 2021.emnlp-main.332)

#### The Case for Index-Free Lexical Search (GrepRAG)

Motivated by how human engineers navigate complex codebases — relying on lightweight terminal utilities rather than semantic search engines — GrepRAG demonstrated that index-free lexical retrieval via standard grep-style tools achieves performance highly competitive with, and often superior to, sophisticated vector-based RAG systems. The core insight is that software logic relies on exact syntax and highly specific, deterministic identifiers. Semantic vector search struggles to locate exact, rigid definitions of custom entities (e.g., `auth_token_v2_middleware_factory`), whereas lexical search locates them instantaneously with no pre-computed index.

Across CrossCodeEval and RepoEval-Updated, GrepRAG achieved a **7.04% to 15.58% relative improvement in exact code match (EM)** over the best graph-based semantic baseline — with zero index construction overhead.

A lightweight post-processing pipeline (identifier-weighted re-ranking + structure-aware deduplication) neutralises GrepRAG's sensitivity to high-frequency ambiguous keywords.

**Reference:** GrepRAG: An Empirical Study and Optimization of Grep-Like Retrieval for Code Completion (ResearchGate publication/400340391)

#### The Case for LLM-Enriched Semantic Metadata

Hierarchical summarisation research demonstrated that when agents must navigate repositories in response to non-technical queries (e.g., bug reports, product manager requests), a severe vocabulary mismatch prevents pure structural indexing from bridging the gap. Generating LLM-produced natural language summaries at the project, directory, and file level allows agents to navigate top-down using semantic intent rather than identifier matching — achieving Pass@10 of 0.89 and Recall@10 of 0.33 on real-world Jira issue datasets.

The key economic insight: this LLM enrichment occurs **once at index build time**, not at query time. The cost is amortised across all future queries, and only changed nodes require re-enrichment. This makes the approach economically viable even at enterprise scale.

**Reference:** Repository-Level Code Understanding by LLMs via Hierarchical Summarization: Improving Code Search and Bug Localisation (ResearchGate publication/391739021)

---

### 10.2 Architecture: Hybrid AST + GrepRAG + LLM Enrichment

The indexing system is implemented in three sequential phases. Phases 1 and 2 are fully deterministic and require no AI tooling. Phase 3 is an LLM enrichment pass that runs once per index build.

The index is not a live mirror of the repository. It is rebuilt at deliberate pipeline phase boundaries — specifically before PREPARE begins and before DEPLOY begins — providing agents with a stable, accurate snapshot at the moments they need it. This aligns with the primary purpose of the index: supplying agent input context, not tracking real-time code state. Individual tickets are expected to be small, focused units of work (see Section 6.3), so the codebase changes introduced by a single ticket are unlikely to invalidate the index in a way that affects agents working on unrelated concerns.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SOURCE REPOSITORY                            │
│              (file system: .kt, .ts, .py, .java, ...)               │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
              ┌─────────────▼──────────────┐
              │   PHASE 1 — AST PARSER     │  ← Deterministic, no LLM
              │                            │
              │  • Parse each file via     │
              │    built-in AST or         │
              │    Tree-sitter grammar     │
              │  • Extract: classes,       │
              │    functions, methods,     │
              │    signatures, docstrings, │
              │    decorators, imports,    │
              │    line ranges             │
              │  • Recursive split-merge   │
              │    into syntactically      │
              │    valid subtrees (cAST)   │
              │  • Write nodes → SQLite    │
              └─────────────┬──────────────┘
                            │
              ┌─────────────▼──────────────┐
              │  PHASE 2 — DEPENDENCY      │  ← Deterministic, no LLM
              │           MAPPER           │
              │                            │
              │  • For each node, ripgrep  │
              │    all call sites and      │
              │    identifier references   │
              │  • Resolve imports to      │
              │    source nodes            │
              │  • Write edges → SQLite    │
              │    (calls, imports,        │
              │     inherits, overrides)   │
              │  • Build scope resolution  │
              │    tree for selective      │
              │    retrieval gating        │
              └─────────────┬──────────────┘
                            │
              ┌─────────────▼──────────────┐
              │  PHASE 3 — LLM ENRICHMENT  │  ← One-time, amortised
              │           PASS             │
              │                            │
              │  • For each node, send:    │
              │    signature + docstring   │
              │    + immediate neighbours  │
              │    (parent, children,      │
              │     callers, callees)      │
              │  • LLM returns:            │
              │    - semantic_summary      │
              │    - domain_tags           │
              │    - inferred_responsibility│
              │  • Store on node in SQLite │
              │  • Re-run only on changed  │
              │    nodes (hash-gated)      │
              └─────────────┬──────────────┘
                            │
              ┌─────────────▼──────────────┐
              │      INDEX AT REST         │
              │    (SQLite database)       │
              │                            │
              │  nodes + edges + metadata  │
              └─────────────┬──────────────┘
                            │
            ┌───────────────┼──────────────────┐
            │               │                  │
┌───────────▼──────┐ ┌──────▼───────┐ ┌────────▼────────┐
│  LEXICAL QUERY   │ │ GRAPH QUERY  │ │ SEMANTIC QUERY  │
│  (GrepRAG-style) │ │ (dependency  │ │ (NL → semantic  │
│                  │ │  traversal)  │ │  summary match) │
│  Exact identifier│ │              │ │                 │
│  lookup via      │ │  Who calls   │ │  "find where    │
│  ripgrep against │ │  what? What  │ │  cart items are │
│  raw source,     │ │  does this   │ │  dropped" →     │
│  post-processed  │ │  depend on?  │ │  domain_tags +  │
│  with re-ranking │ │              │ │  summary search │
└──────────────────┘ └──────────────┘ └─────────────────┘
```

#### Query Path Selection

The system gates which query path to invoke before hitting the index:

```
Incoming query
    │
    ├─ Contains exact identifier (function name, class, symbol)?
    │       → Lexical path (GrepRAG) — fastest, most precise
    │
    ├─ Requests dependency chain or call graph?
    │       → Graph traversal path
    │
    ├─ Natural language / non-technical description?
    │       → Semantic path (LLM summary matching)
    │
    └─ Ambiguous → Lexical first, fall back to semantic on empty result
```

This mirrors the Repoformer selective retrieval finding — approximately 80% of agent queries in a structured engineering pipeline involve exact identifier resolution (function names, class references, import targets), meaning the cheap lexical path handles the large majority of load. The semantic path is reserved for natural language queries, primarily from PLAN-phase agents working from product requirements.

---

### 10.3 Database Schema

The index is stored in a single SQLite database (`codeindex.db`) co-located with the project. SQLite is chosen for zero infrastructure overhead, git-friendliness, and sufficient performance at the scale of a single enterprise repository.

```sql
-- Core node table: one row per syntactically complete code unit
CREATE TABLE nodes (
    id                  TEXT PRIMARY KEY,       -- {file_path}::{node_type}::{name}
    file_path           TEXT NOT NULL,
    node_type           TEXT NOT NULL,          -- 'file' | 'class' | 'function' | 'method'
    name                TEXT NOT NULL,
    signature           TEXT,                   -- full function/method signature
    docstring           TEXT,                   -- extracted docstring if present
    start_line          INTEGER NOT NULL,
    end_line            INTEGER NOT NULL,
    raw_source          TEXT,                   -- full source of the AST subtree
    content_hash        TEXT NOT NULL,          -- SHA256 of raw_source; gates re-enrichment
    -- LLM-enriched fields (NULL until Phase 3 runs)
    semantic_summary    TEXT,
    domain_tags         TEXT,                   -- JSON array, e.g. ["auth", "session", "jwt"]
    inferred_responsibility TEXT,
    enriched_at         TEXT                    -- ISO timestamp of last enrichment
);

-- Dependency edge table: directed graph of code relationships
CREATE TABLE edges (
    source_id           TEXT NOT NULL REFERENCES nodes(id),
    target_id           TEXT NOT NULL REFERENCES nodes(id),
    edge_type           TEXT NOT NULL,          -- 'calls' | 'imports' | 'inherits' | 'overrides' | 'references'
    call_site_line      INTEGER,                -- line number where the relationship occurs
    PRIMARY KEY (source_id, target_id, edge_type)
);

-- File registry: tracks file-level change detection
CREATE TABLE files (
    path                TEXT PRIMARY KEY,
    last_modified       TEXT NOT NULL,          -- ISO timestamp
    content_hash        TEXT NOT NULL,          -- SHA256 of full file content
    language            TEXT NOT NULL,          -- 'kotlin' | 'typescript' | 'python' | ...
    indexed_at          TEXT NOT NULL
);

-- Index metadata: tracks build state
CREATE TABLE index_meta (
    key                 TEXT PRIMARY KEY,
    value               TEXT NOT NULL
);
-- Example rows:
-- ('schema_version', '1')
-- ('last_full_build', '2026-03-24T10:00:00Z')
-- ('last_incremental_update', '2026-03-25T14:32:00Z')
-- ('enrichment_model', 'claude-sonnet-4-6')
```

**Key indices:**

```sql
CREATE INDEX idx_nodes_file_path   ON nodes(file_path);
CREATE INDEX idx_nodes_name        ON nodes(name);
CREATE INDEX idx_nodes_node_type   ON nodes(node_type);
CREATE INDEX idx_edges_source      ON edges(source_id);
CREATE INDEX idx_edges_target      ON edges(target_id);
CREATE INDEX idx_edges_type        ON edges(edge_type);
```

---

### 10.4 Use Cases

#### UC-1: PREPARE Agent Navigates the Index for Architecture Spike

**Actor:** `architect-agent`
**Trigger:** Card moves into Architecture Spike column
**Query type:** Semantic + graph

The architect agent needs to understand how the existing authentication subsystem is structured before proposing changes. It queries the semantic path with "authentication and session management," retrieving `domain_tags` matches across the index. It then traverses the graph edges outward from those nodes to map the dependency chain. The agent receives a curated set of node summaries and signatures — no raw source — sufficient to reason about the architecture without exhausting its context window.

```
Query: "authentication and session management"
  → Semantic search on domain_tags + semantic_summary
  → Returns: AuthService, JwtValidator, SessionFactory nodes (summaries only)
  → Graph traversal: edges from AuthService → SessionFactory → UserRepo
  → Agent context: ~15 node summaries + dependency map
  → Raw source: NOT loaded (PREPARE phase)
```

---

#### UC-2: BUILD Agent Resolves a Cross-File Function Call

**Actor:** `engineer-agent`
**Trigger:** Implementing a ticket that requires calling an existing utility function
**Query type:** Lexical (GrepRAG)

The engineer agent encounters an unresolved reference to `validateCartState` in the code it is writing. It issues a lexical query against the index. The GrepRAG path runs ripgrep across the repository for the exact identifier, re-ranks results by identifier specificity, deduplicates, and returns the single matching node with its full raw source and signature.

```
Query: exact identifier "validateCartState"
  → Lexical ripgrep search
  → Re-ranking: rare identifier → top result is unambiguous
  → Returns: CartValidator::validateCartState — signature + raw_source
  → Agent receives: exact function body, ready to call correctly
```

---

#### UC-3: Phase-Boundary Re-index

**Actor:** Pipeline orchestrator (triggered by phase transition)
**Trigger:** Operator moves a card into Architecture Spike (entering PREPARE) or into QA (entering DEPLOY)

The index is rebuilt as a full three-phase pass over the repository at the start of each relevant phase boundary. Because tickets represent small, focused units of work, a full rebuild is fast and ensures agents start from a clean, consistent snapshot. The `content_hash` column on each node gates Phase 3 — only nodes whose source has changed since the last build incur an LLM enrichment call, keeping cost proportional to the actual delta.

```
Trigger: card enters Architecture Spike
  → Phase 1: Parse all files → extract/update nodes in SQLite
  → Phase 2: Re-resolve all dependency edges across repo
  → Phase 3: Compare content_hash per node vs. stored value
              → Only changed nodes sent to LLM for enrichment
              → Unchanged nodes retain existing semantic_summary
  → Index marked as current for this phase boundary
  → Architect agent receives fresh snapshot

Trigger: card enters QA
  → Same three-phase pass runs again
  → Reflects all code committed during BUILD phase
  → QA and DoD agents work from post-implementation index
```

---

#### UC-4: PLAN Agent Localises a Bug Report

**Actor:** `pm-agent` or `architect-agent`
**Trigger:** Operator creates a ticket with a non-technical bug description
**Query type:** Semantic (hierarchical navigation)

A ticket arrives: "Users report their cart loses items after applying a discount code." The agent has no knowledge of exact identifier names. It queries the semantic path, which searches `semantic_summary` and `domain_tags` for concepts matching "cart," "discount," and "state mutation." It navigates top-down from file-level summaries to function-level nodes, identifying `CartService::applyDiscount` and `CartStateManager::recomputeItems` as the most likely suspects, before retrieving their raw source for analysis.

```
Query: "cart loses items after applying discount code"
  → Semantic search: domain_tags ∋ ["cart", "discount"] AND semantic_summary ∋ "state mutation"
  → Candidate nodes ranked by relevance
  → Top matches: CartService::applyDiscount, CartStateManager::recomputeItems
  → Agent receives: summaries first → confirms relevance → requests raw_source
  → Result: pinpointed to 2 functions without loading the full codebase
```

---

### 10.5 Implementation Stack

| Concern | Tool | Notes |
|---------|------|-------|
| Python AST parsing | `ast` (stdlib) | Python files |
| Multi-language parsing | `tree-sitter` + grammars | Kotlin, TypeScript, Java, Go |
| Lexical search | `ripgrep` via subprocess | GrepRAG retrieval path |
| Index store | `sqlite3` (stdlib) | Zero infrastructure overhead |
| LLM enrichment | Claude API (configurable) | Phase 3 only; hash-gated per node |
| Content hashing | `hashlib` SHA-256 (stdlib) | Gates Phase 3 re-enrichment |

---

### 10.6 Relationship to Open Design Decisions

This design resolves **OD-05** (Codebase index implementation) in favour of a custom lightweight indexer rather than adopting Shotgun or similar third-party tools. The rationale: the hybrid AST + GrepRAG approach requires tight control over the chunking phase (cAST semantics), the dependency graph schema (tailored to agent query patterns), and the incremental update logic (hash-gated LLM enrichment). A third-party tool would either not expose these primitives or would add unnecessary abstraction.

**Index staleness** (noted in Section 8 as a Known Hard Problem) is resolved by the phase-boundary rebuild model in UC-3. The index is not intended to be a live mirror of the repository — it is a stable context snapshot for agents at the moments they need it. Rebuilding at phase boundaries (before PREPARE, before DEPLOY) is sufficient because tickets are small units of work and agents do not need real-time code tracking; they need accurate context at the start of their task. This design decision also reinforces the expectation that operators keep tickets small and focused — large, multi-week implementation tickets would strain the phase-boundary model, but that is by design: they should not exist in the pipeline.
