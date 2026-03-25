# Standard Template

Use this template for **Standard** depth. Target: 3-6 pages. Enough detail for an engineer to implement from. The default for most features.

---

```markdown
# Tech Spec: [Feature Name]

**Date:** [date]
**Depth:** Standard
**Status:** Draft
**PRD:** [link or filename, if available]
**Event Catalog:** [link or "N/A"]

---

## 1. Context & Goals

### Problem Statement
[What problem does this feature solve? Who has this problem? What's the impact of not solving it?]

### Goals
1. [Measurable goal 1]
2. [Measurable goal 2]
3. [Measurable goal 3]

### Non-Goals
- [Explicitly out of scope 1]
- [Explicitly out of scope 2]

### Background
[Context a reader needs — existing system behavior, prior decisions, related features. Keep brief.]

---

## 2. Architecture Approach

### High-Level Design

[Where does this feature live? Which module/service/bounded context? How does it fit with existing architecture?]

```
[Component or flow diagram — ASCII art]
```

### Design Decisions

**Decision 1: [Short title]**
- **Chosen:** [What]
- **Alternatives:** [What was rejected]
- **Rationale:** [Why]
- **Trade-offs:** [What we give up]

**Decision 2: [Short title]**
- **Chosen:** [What]
- **Alternatives:** [What was rejected]
- **Rationale:** [Why]

### Module/Package Placement

```
com.example.{service}/
├── domain/model/        → [what goes here]
├── domain/event/        → [what goes here]
├── application/command/  → [what goes here]
├── infrastructure/...    → [what goes here]
└── api/controller/       → [what goes here]
```

---

## 3. API Design

### [Endpoint 1]: [HTTP Method] [Path]

**Purpose:** [What this endpoint does]

**Request:**
```json
{
  "field1": "type — description",
  "field2": "type — description (optional)"
}
```

**Response (200):**
```json
{
  "field1": "type — description",
  "field2": "type — description"
}
```

**Error Responses:**

| Status | Code | When |
|--------|------|------|
| 400 | `INVALID_REQUEST` | [Condition] |
| 404 | `NOT_FOUND` | [Condition] |
| 409 | `CONFLICT` | [Condition] |

**Validation Rules:**
- `field1`: required, non-blank, max length
- `field2`: optional, specific format

**Idempotency:** [Strategy, if applicable]

### [Endpoint 2]: [HTTP Method] [Path]
[repeat structure]

---

## 4. Data Model

### [Entity Name]

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | UUID | PK, generated | |
| [field] | [type] | [constraints] | [notes] |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |
| updated_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**Indexes:**
- `idx_{table}_{field}` on `{field}` — used by [query]

**State Machine (if applicable):**
```
STATE_A ──▶ STATE_B ──▶ STATE_C
   │                      │
   └──▶ STATE_D ◀─────────┘
```

### Migration

```sql
-- V{NNN}__{description}.sql

CREATE TABLE [table] (
    [columns]
);

CREATE INDEX [index_name] ON [table]([columns]);
```

### Aggregate Boundaries (if DDD)
- **Aggregate root:** [Entity]
- **Invariants:** [Business rules enforced]
- **Owned entities:** [Children within boundary]

---

## 5. Integration Points

### [Integration Name]

**Type:** Sync API | Kafka event | Async job
**Direction:** Outbound | Inbound
**Target:** [Service name]

**Contract:**
```json
{
  "eventType": "[EventName]",
  "field1": "type",
  "field2": "type"
}
```

**Failure Handling:**
- Timeout: [value]
- Retries: [strategy]
- Circuit breaker: [threshold]
- Fallback: [behavior when down]

### Domain Events Published

| Event | Topic | Partition Key | When |
|-------|-------|--------------|------|
| [EventName] | [topic] | [key] | [trigger condition] |

---

## 6. Non-Functional Requirements

### Performance
| Metric | Target | Context |
|--------|--------|---------|
| p99 latency | [X]ms | [which endpoint/operation] |
| Throughput | [X] RPS | [peak expected load] |

### Security
- **Authentication:** [method]
- **Authorization:** [who can do what]
- **Input validation:** [approach summary]
- **Data sensitivity:** [PII handling]

### Caching
- **Strategy:** [None / Redis / In-memory]
- **TTL:** [if applicable]
- **Invalidation:** [how/when]

---

## 7. Open Questions & Risks

### Open Questions
| # | Question | Blocking? | Impact |
|---|----------|-----------|--------|
| Q1 | [question] | Yes/No | [how it affects design] |

### Risks
| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | [risk] | Low/Med/High | Low/Med/High | [strategy] |
```