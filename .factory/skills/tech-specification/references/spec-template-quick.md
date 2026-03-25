# Quick Overview Template

Use this template for **Quick Overview** depth. Target: 1-2 pages. Concise, decision-focused, good for alignment checks and early exploration.

---

```markdown
# Tech Spec: [Feature Name] — Quick Overview

**Date:** [date]
**Depth:** Quick Overview
**Status:** Draft

---

## Problem & Goals

[3-5 sentences. What problem does this solve? For whom? What does success look like?]

## Architecture Approach

[1 paragraph describing where this lives in the system and the high-level approach. Include a simple ASCII diagram if it clarifies the flow.]

```
[optional: simple component or flow diagram]
```

**Key rationale:** [Why this approach over alternatives, in 1-2 sentences]

## Key Design Decisions

| Decision | Chosen | Why |
|----------|--------|-----|
| [Decision 1] | [Choice] | [Brief rationale] |
| [Decision 2] | [Choice] | [Brief rationale] |
| [Decision 3] | [Choice] | [Brief rationale] |

## API Surface

| Method | Path | Purpose |
|--------|------|---------|
| [POST] | [/api/v1/...] | [What it does] |
| [GET] | [/api/v1/...] | [What it does] |

## Data Model Summary

**New entities:** [Entity1 (fields: a, b, c), Entity2 (fields: x, y, z)]
**Modified entities:** [Entity3 — adding field: status]
**Relationships:** [Entity1 has-many Entity2]

## Risks & Open Questions

- [Risk or question 1]
- [Risk or question 2]
- [Risk or question 3]
```