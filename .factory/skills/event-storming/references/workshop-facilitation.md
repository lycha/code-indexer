# Workshop Facilitation Guide

## Overview

This guide provides detailed phase orchestration for AI-simulated event storming workshops.

## Phase Structure

### Phase 1: Chaotic Exploration (~40% of time)

**Goal:** Generate as many events as possible without constraints.

**Orchestration:**

1. Launch all 5 persona agents in parallel with domain context
2. Each persona generates events from their perspective
3. No filtering, no ordering, no criticism
4. Collect all outputs

**Prompts for agents:**

```markdown
# Domain Expert
"Identify all business events that occur in {domain}. Include:
- Regular operations events
- Exception/error events
- Time-based events (end of day, monthly)
- Regulatory/compliance events"

# Developer
"Identify technical events in {domain}. Include:
- System-generated events
- Integration events
- Error/failure events
- Async completion events"

# Business Analyst
"For {domain}, identify:
- Commands that users issue
- Actors who issue those commands
- What events each command triggers"

# Product Owner
"For {domain}, identify:
- Which events provide user value
- MVP-essential events
- Events that could be deferred"

# Devil's Advocate
"For {domain}, identify:
- Missing scenarios
- Edge cases not mentioned
- What could go wrong"
```

**Output synthesis:**

- Collect all events
- Tag with [Persona] attribution
- Don't filter or deduplicate yet

### Phase 2: Timeline Ordering (~15% of time)

**Goal:** Arrange events in chronological order.

**Process:**

1. Start with the initiating event (what kicks things off)
2. Work forward through the happy path
3. Branch for alternative flows
4. Include exception paths

**Facilitation prompts:**

```markdown
"Given these events, arrange them chronologically:
1. What event starts the process?
2. What must happen next?
3. Where do branches occur?
4. What ends the process?"
```

**Output:**

```markdown
## Timeline

### Happy Path
1. [Event 1] - triggers →
2. [Event 2] - triggers →
3. [Event 3]

### Alternative: [Condition]
2a. [Alternative Event]

### Exception: [Failure Case]
2e. [Exception Event]
```

### Phase 3: Command Discovery (~15% of time)

**Goal:** Identify what triggers each event.

**Process:**

1. For each event, ask "what caused this?"
2. Commands are explicit actions (user clicks, API calls)
3. Some events are triggered by other events (policies)
4. Some events are time-triggered (schedulers)

**Facilitation prompts:**

```markdown
"For each event, identify:
- What command triggers it? (or)
- What event triggers it? (policy) (or)
- What time/schedule triggers it?"
```

**Output:**

```markdown
## Command Map

| Event | Triggered By | Type |
| --- | --- | --- |
| Order Placed | Place Order command | User action |
| Inventory Reserved | Order Placed event | Policy |
| Daily Report Generated | 5:00 AM schedule | Time trigger |
```

### Phase 4: Actor Identification (~10% of time)

**Goal:** Map who issues each command.

**Process:**

1. For each command, ask "who can do this?"
2. Identify both human actors and systems
3. Note roles and permissions
4. Identify external actors (customers, partners)

**Actor types:**

- Internal users (by role)
- External users (customers, vendors)
- Internal systems
- External systems
- Time/schedulers

**Output:**

```markdown
## Actor Map

### Customer
- Place Order
- Cancel Order
- Request Refund

### Admin
- Override Price
- Cancel Any Order
- Process Refund

### Order Service (System)
- Reserve Inventory
- Release Inventory
- Notify Customer
```

### Phase 5: Bounded Context Discovery (~15% of time)

**Goal:** Group related events into bounded contexts.

**Process:**

1. Look for clusters of related events
2. Identify where language/terminology changes
3. Find natural boundaries (team ownership, data ownership)
4. Check for aggregates that group events

**Context discovery signals:**

- Same terminology cluster
- Same actor set
- Same data/aggregate
- Same team ownership
- Natural transactional boundary

**Facilitation prompts:**

```markdown
"Looking at these events, where do you see:
- Clusters of related events?
- Changes in terminology?
- Different data ownership?
- Natural team boundaries?"
```

**Output:**

```markdown
## Bounded Contexts

### Ordering Context
**Events:** Order Placed, Order Confirmed, Order Shipped
**Commands:** Place Order, Confirm Order, Ship Order
**Aggregates:** Order
**Type:** Core Domain

### Inventory Context
**Events:** Inventory Reserved, Inventory Released
**Commands:** Reserve Inventory, Release Inventory
**Aggregates:** Product, Warehouse
**Type:** Supporting Domain
```

### Phase 6: Hot Spot Resolution (~5% of time)

**Goal:** Address conflicts and gaps.

**Process:**

1. Review all hot spots identified
2. Discuss each with relevant personas
3. Reach resolution or document as TODO
4. Prioritize unresolved items

**Hot spot types:**

- Conflicting requirements
- Missing scenarios
- Unclear ownership
- Technical vs business disagreement
- Ambiguous terminology

**Resolution approaches:**

- Clarify with domain expert
- Accept multiple valid interpretations
- Defer decision with explicit TODO
- Document as ADR if significant

**Output:**

```markdown
## Hot Spots

### Resolved
- [Issue]: [Resolution] - agreed by [personas]

### Deferred
- [Issue]: [Why deferred] - TODO: [action needed]
```

## Timing Guidelines

For a typical domain:

| Phase | Time % | Activities |
| --- | --- | --- |
| 1. Chaotic Exploration | 40% | Parallel persona brainstorming |
| 2. Timeline Ordering | 15% | Chronological arrangement |
| 3. Command Discovery | 15% | Trigger identification |
| 4. Actor Identification | 10% | Who does what |
| 5. Bounded Context Discovery | 15% | Grouping and boundaries |
| 6. Hot Spot Resolution | 5% | Conflict resolution |

## Synthesis Best Practices

### Deduplication

Events from different personas may overlap. When synthesizing:

1. Keep unique perspective details
2. Merge identical events, noting all sources
3. Flag contradicting views as hot spots

### Attribution

Always track provenance:

```markdown
- Order Placed [Domain Expert, Business Analyst]
- Payment Timeout [Developer, Devil's Advocate]
```

### Quality Signals

**Good event storm:**

- Multiple perspectives represented
- Both happy path and exceptions
- Clear bounded contexts
- Resolved hot spots
- Actionable output

**Warning signs:**

- Single perspective dominates
- No exception scenarios
- Unclear boundaries
- Many unresolved hot spots
- Vague events

---

**Related:** `persona-prompts.md`, `bounded-context-discovery.md`
