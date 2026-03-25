---
name: code-review
description: 'Perform Staff SWE code reviews for Kotlin/Spring backend changes with severity-tagged findings and a saved report. Use when: code review, review PR, review changes, review uncommitted changes.'
---

# Code Review (Kotlin/Spring Backend)

Perform a Staff Software Engineer-level review of backend changes with clear severity levels, actionable recommendations, and a saved report.

## When to Use This Skill
- Reviewing uncommitted changes, branches, or PRs for Kotlin/Spring backend code.
- Reviewing implementation plans or design docs for backend changes.
- Auditing security- or data-sensitive changes before merge.

## What You'll Need
- Review target (uncommitted changes, branch range, commit range, or specific files).
- Context on intended behavior and any known risks (auth, data migrations, integrations).
- Any required conventions (`CLAUDE.md`, `CONVENTIONS.md`, OpenAPI constraints).

## Process

### Step 1: Identify scope and source of changes
1. If this is a git repo, capture the diff scope:
   - `git status --porcelain`
   - `git diff --stat`
   - `git diff <base>...<head>` or `git diff HEAD` for uncommitted changes
2. If the repo is not git-based, ask the user to supply the file list or diff.
3. Identify generated code and exclude it from review unless explicitly requested.

### Step 2: Read project conventions and context
1. Read `CLAUDE.md` and `CONVENTIONS.md` for style and review guidance.
2. Note framework conventions (OpenAPI generation, JOOQ, Flyway) and required patterns.
3. Ask clarifying questions when requirements or behavior are unclear.

### Step 3: Review for Kotlin/Spring risk areas
Use the verification checklist in `references/verification-checklist.md` and focus on:
- **Correctness:** null safety, validation, error handling, edge cases.
- **Security:** JWT handling, redirects, cookies, auth filters, input validation.
- **Data integrity:** transaction boundaries, idempotency, migrations, JOOQ usage.
- **API contracts:** OpenAPI alignment, status codes, backward compatibility.
- **Observability:** logging, metrics, trace propagation.
- **Performance:** blocking calls, inefficient queries, excessive allocations.
- **Tests:** unit + integration coverage for critical paths.

### Step 4: Record findings with severity
1. Use severity definitions from `references/severity-levels.md`.
2. Each finding should include: ID, severity, file/function, problem, impact, and recommendation.
3. Capture positive observations that should be preserved.

### Step 5: Produce report and ask for review
1. Save the report to `code-review/REVIEW-YYYYMMDD-HHMMSS.md` using `references/review-template.md`.
2. Provide a concise chat summary: counts by severity and overall verdict.
3. Include a **Draft PR Summary** section and ask the user to review it.

## Output Template
Use the structure from `references/review-template.md`.

## References
- `references/severity-levels.md`
- `references/review-template.md`
- `references/verification-checklist.md`
