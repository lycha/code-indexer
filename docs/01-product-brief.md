# Product Brief: AI Engineering Operating System

**Author:** Kris
**Date:** March 24, 2026
**Status:** In Discovery

---

## Problem

### Who has this problem?
Engineering managers and senior engineers who have real SDLC expertise and want to apply it *to* AI — not just use AI as a faster text editor, but run a full AI-powered product squad with the same rigour they'd apply to a human team.

### What is the problem?
AI coding tools today are point solutions. They augment individual contributors but don't model how a *team* works. There is no system that mirrors the structure of a real product squad — specialised roles, artifact handoffs, quality gates, and a human operator at the centre making judgment calls.

The result: experienced engineering managers can't apply their process knowledge to AI. They're stuck either micromanaging individual AI interactions or surrendering control to opaque automation pipelines.

### Evidence
- The roles in a product squad exist because the work genuinely requires different skills and contexts. No single AI prompt replaces that structure.
- Current agentic tools (LangChain, AutoGen, CrewAI, Shotgun) are either developer-facing pipelines or single-agent tools. None model the full SDLC as an operator-controlled workflow.
- The "149 ideas" problem is real and spreading: vibe coding accelerates idea generation faster than any individual can process, and existing tools offer no structured way to move ideas through a quality-controlled pipeline.
- Engineering managers bring years of accumulated process knowledge (sprint rituals, review gates, escalation paths, definition of done) that has nowhere to go in current AI tooling.

### Impact of not solving
AI adoption in software teams stays shallow. Individuals use AI as a faster autocomplete. Teams never get leverage at the process level — the place where an experienced EM's judgment actually compounds.

---

## Why Now

- **Model capability:** LLMs are now capable enough to hold role-specific context (architect vs. QA vs. engineer) across multi-step tasks with consistent quality.
- **Agentic infrastructure is ready:** MCP, Agent SDKs, and tool-calling provide the plumbing to build multi-agent systems without starting from scratch.
- **The EM gap is unaddressed:** There is a clear and growing gap between "AI for individual devs" and "AI for the whole team." No product has credibly filled it.
- **The problem is being felt now:** PMs and EMs are building their own ad hoc solutions (Kanban boards, Claude skills, custom pipelines) out of desperation. The market is signalling readiness.

---

## Proposed Solution

An **AI Engineering Operating System** — a Kanban-based interface where each column maps to a stage in the SDLC pipeline, and a configured AI agent performs a specific transformation on the ticket as it passes through. The human operator controls flow, reviews outputs, and maintains final authority over every significant decision.

### What this IS
- A pipeline of artifact transformations: a ticket enters each column as one thing and leaves as a richer, more refined thing
- A library of AI agents modelled on real squad roles, each with a defined skill set, context scope, and input/output contract
- A Kanban board as the operator UX with phase groups (PLAN → PREPARE → BUILD → DEPLOY) containing columns
- Human-in-the-loop gates at configurable points — auto-advance or require approval, per column
- A git-backed document tree as the artifact store, with full history of every agent and human contribution
- Severity-tagged review findings (INFO / WARNING / BLOCKER) with BLOCKER always escalating to the human operator

### What this is NOT
- A fully automated CI/CD pipeline
- A replacement for a real engineering team
- An IDE plugin or code editor
- A project management tool (though integration is a future consideration)

### MVP Concept
Five agents (PM, Architect, Engineer, QA, Reviewer), four phase groups, a working Kanban board, and a git-backed artifact store. The human operator seeds a ticket, agents transform it stage by stage, the operator approves transitions. No parallel workstreams in v1.

---

## Impact & Effort

| Metric | Current | Target | Confidence |
|--------|---------|--------|------------|
| SDLC stages with meaningful AI leverage | 1–2 | 6–8 | High |
| Time from idea to deployable code (small features) | Days | Hours | Medium |
| Operator visibility into AI reasoning and output | Low | High | High |

**T-shirt size:** M (MVP), L (full system)
**Confidence:** Medium — core infrastructure exists; integration and operator UX are the unknowns

---

## The Ask

- [ ] Commit to building the MVP as the primary project
- [ ] Complete the PRD and system design doc as the next artifacts
- [ ] Define the full agent roster and column map before writing any code

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Agent output quality too inconsistent across roles | H | M | Invest heavily in per-role agent specs and rubrics |
| Operator UX becomes the bottleneck | M | H | Start with minimal board; ship fast and iterate |
| Scope creep into full IDE or PM tool | M | H | Hard scope boundary; defer integrations |
| Codebase index goes stale mid-ticket | M | M | Refresh index before DEPLOY; flag as known problem |

---

## Open Questions

- [ ] What is the right operator interface for MVP — existing Kanban tool, custom UI, or CLI?
- [ ] How do agents share context across handoffs — structured handoff docs, shared memory, or both?
- [ ] What columns live inside each phase group?
- [ ] How do the ticket DoD and the column rubric coexist in the reviewer agent's context?
