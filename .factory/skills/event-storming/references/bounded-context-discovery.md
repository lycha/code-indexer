# Bounded Context Discovery

## Overview

Bounded contexts are semantic boundaries within which a domain model has consistent meaning. Event storming helps discover these boundaries through patterns in events, language, and ownership.

## Discovery Signals

### Language Patterns

**Same term, different meaning:**

When the same word means different things:

```text
"Customer" in Sales = potential buyer, lead
"Customer" in Support = account holder with issues
"Customer" in Billing = payment entity
```

**Different terms, same thing:**

When different areas use different words for the same concept:

```text
Sales: "Prospect" → Marketing: "Lead" → CRM: "Contact"
```

**These are context boundaries.**

### Clustering Patterns

**Event clusters:**

Events that naturally group together:

```text
Cluster 1: Order Placed, Order Confirmed, Order Shipped, Order Delivered
Cluster 2: Payment Initiated, Payment Authorized, Payment Captured, Payment Failed
Cluster 3: Item Added to Cart, Cart Updated, Cart Abandoned
```

**Actor clusters:**

Different actors operate in different contexts:

```text
Customer-facing: Customer, Sales Rep
Operations-facing: Warehouse Staff, Shipping Coordinator
Finance-facing: Accountant, CFO
```

### Ownership Patterns

**Team ownership:**

What team would own this in an ideal world?

```text
Order Team: Order lifecycle events
Payment Team: Payment processing events
Logistics Team: Shipping and delivery events
```

**Data ownership:**

Who is the source of truth for this data?

```text
Customer Profile: CRM owns
Order Data: Order Service owns
Inventory Levels: Warehouse owns
```

## Discovery Process

### Step 1: Identify Pivot Events

**Pivot events** are where context might change:

```text
Order Placed → [PIVOT] → Inventory Reserved
Payment Received → [PIVOT] → Order Fulfilled
Shipment Delivered → [PIVOT] → Invoice Generated
```

Ask: "Does the language/meaning change here?"

### Step 2: Test with Ubiquitous Language

For each potential context, test:

- Does "Order" mean the same thing throughout?
- Do all stakeholders use the same terms?
- Is there a natural glossary for this area?

If language is consistent within a group but changes at boundaries → you found a context.

### Step 3: Validate with Aggregates

Aggregates typically live within one context:

```text
Order Context:
  - Order (aggregate root)
  - OrderLine
  - DeliveryAddress

Inventory Context:
  - Product (aggregate root)
  - Warehouse
  - StockLevel
```

If an aggregate seems to span contexts, reconsider boundaries.

### Step 4: Check Integration Points

Where contexts meet, you need integration:

```text
Order Context ←→ Inventory Context
  - Order Placed event shared
  - Inventory Reserved confirmation needed
  - Language translation may be needed
```

## Context Types

### Core Domain

**What it is:** The unique business value, competitive advantage.

**Characteristics:**

- Most business logic complexity
- Highest investment priority
- Custom-built, not purchased
- Domain experts deeply involved

**Example:** Order matching algorithm in a trading platform.

### Supporting Domain

**What it is:** Necessary but not differentiating.

**Characteristics:**

- Supports core domain
- Can be simplified or outsourced
- Less complexity investment
- May be generic with customization

**Example:** User management, notifications.

### Generic Domain

**What it is:** Common functionality, well-understood solutions exist.

**Characteristics:**

- Buy or use existing solution
- Low differentiation
- Standard patterns
- Minimal customization

**Example:** Authentication, email sending, payment processing.

## Context Mapping Relationships

### Partnership

Two teams work together, succeed or fail together.

```text
[Team A Context] ←Partnership→ [Team B Context]
```

### Shared Kernel

Teams share a common model subset.

```text
[Context A] ←Shared Kernel→ [Context B]
           (shared model)
```

### Customer-Supplier

Upstream supplies, downstream consumes.

```text
[Supplier Context] →downstream→ [Customer Context]
```

### Conformist

Downstream conforms to upstream model.

```text
[Upstream Context] →conforms→ [Downstream Context]
```

### Anticorruption Layer

Downstream protects itself from upstream model.

```text
[External Context] →ACL→ [Our Context]
```

### Open Host Service

Upstream provides a well-defined protocol.

```text
[Our Context] →OHS→ [Multiple Consumers]
```

### Published Language

Shared language between contexts.

```text
[Context A] ←Published Language→ [Context B]
            (e.g., JSON Schema)
```

## Visualization

### Context Map Diagram

```text
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  ┌───────────────┐        ┌───────────────┐                │
│  │   Ordering    │←─ACL──→│   Inventory   │                │
│  │   Context     │        │   Context     │                │
│  │   (Core)      │        │   (Support)   │                │
│  └───────────────┘        └───────────────┘                │
│         │                        │                          │
│         │ Customer-Supplier      │                          │
│         ↓                        ↓                          │
│  ┌───────────────┐        ┌───────────────┐                │
│  │   Shipping    │        │   Billing     │                │
│  │   Context     │        │   Context     │                │
│  │   (Support)   │        │   (Generic)   │                │
│  └───────────────┘        └───────────────┘                │
│                                  │                          │
│                                  │ OHS                      │
│                                  ↓                          │
│                           ┌─────────────┐                   │
│                           │  Payment    │                   │
│                           │  Gateway    │                   │
│                           │  (External) │                   │
│                           └─────────────┘                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Text Representation

```markdown
## Context Map

### Ordering Context (Core Domain)
- Owns: Order, OrderLine, OrderStatus
- Events: Order Placed, Order Confirmed, Order Cancelled
- Upstream of: Shipping, Billing
- Protected from: Inventory (via ACL)

### Inventory Context (Supporting Domain)
- Owns: Product, StockLevel, Warehouse
- Events: Inventory Reserved, Inventory Released
- Downstream of: Ordering (conformist)
```

## Anti-Patterns

### Big Ball of Mud

**Symptom:** No clear boundaries, everything connects to everything.

**Fix:** Start identifying clusters, introduce boundaries gradually.

### Overly Granular Contexts

**Symptom:** Too many tiny contexts, high integration overhead.

**Fix:** Merge contexts that don't have meaningful language differences.

### Context Per Entity

**Symptom:** Every entity becomes a context.

**Fix:** Contexts group related concepts, not individual entities.

### Ignoring Team Structure

**Symptom:** Contexts don't align with how teams work.

**Fix:** Consider Conway's Law - context boundaries often align with team boundaries.

## Validation Checklist

Before finalizing context boundaries:

- [ ] Language is consistent within each context
- [ ] Aggregates don't span contexts
- [ ] Integration points are identified
- [ ] Context types are classified (Core/Supporting/Generic)
- [ ] Relationships between contexts are mapped
- [ ] Team ownership is clear or assignable
- [ ] Data ownership is clear

---

**Related:** `workshop-facilitation.md`, `sticky-note-types.md`
