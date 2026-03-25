# Sticky Note Types

## Overview

Event Storming uses colored sticky notes to represent different concepts. This guide documents the standard color conventions.

## Color Reference

| Color | Represents | Shape | Example |
| --- | --- | --- | --- |
| 🟧 **Orange** | Domain Event | Square | "Order Placed" |
| 🟦 **Blue** | Command | Square | "Place Order" |
| 🟨 **Yellow** (small) | Actor/Person | Small square | "Customer" |
| 🟨 **Yellow** (large) | Aggregate | Large square | "Order" |
| 🟩 **Green** | Read Model | Square | "Order Summary" |
| 🟪 **Purple/Lilac** | Policy | Square | "When order placed, reserve inventory" |
| 🟫 **Pink** | External System | Square | "Payment Gateway" |
| ❗ **Red/Hot Pink** | Hot Spot | Square | Problem or question |

## Detailed Descriptions

### Domain Events (Orange) 🟧

**What they represent:**

- Things that happen in the domain
- Facts that have occurred
- State changes

**Naming convention:**

- Past tense verb phrases
- "Something Happened" format
- Business language, not technical

**Examples:**

- ✅ "Order Placed"
- ✅ "Payment Received"
- ✅ "Shipment Dispatched"
- ❌ "PlaceOrder" (command, not event)
- ❌ "OrderService.Create()" (technical, not business)

**Subcategories:**

- **Business Events** - Core domain happenings
- **Time Events** - "End of Day Reached", "Month Closed"
- **Error Events** - "Payment Failed", "Timeout Occurred"

### Commands (Blue) 🟦

**What they represent:**

- Explicit actions that trigger events
- User intentions
- API operations

**Naming convention:**

- Imperative verb phrases
- "Do Something" format
- Action-oriented

**Examples:**

- ✅ "Place Order"
- ✅ "Approve Request"
- ✅ "Cancel Subscription"
- ❌ "Order Placed" (that's an event)
- ❌ "OrderHandler" (technical implementation)

**Relationship to events:**

```text
Command → (processed by) → Aggregate → (emits) → Event
Place Order → Order → Order Placed
```

### Actors (Yellow - Small) 🟨

**What they represent:**

- Who or what issues commands
- Human roles
- Systems that initiate actions

**Types of actors:**

- **Internal Users** - By role (Admin, Manager, Sales Rep)
- **External Users** - Customers, Vendors, Partners
- **Systems** - Internal services, Schedulers
- **External Systems** - Third-party APIs

**Examples:**

- ✅ "Customer"
- ✅ "Sales Manager"
- ✅ "Inventory System"
- ✅ "Daily Scheduler"

### Aggregates (Yellow - Large) 🟨

**What they represent:**

- Business entities that process commands
- Transactional boundaries
- State holders

**Naming convention:**

- Noun (singular)
- Business entity name

**Examples:**

- ✅ "Order"
- ✅ "Customer"
- ✅ "Product"
- ✅ "Shipment"

**Relationship mapping:**

```text
Actor → Command → Aggregate → Event
Customer → Place Order → Order → Order Placed
```

### Read Models (Green) 🟩

**What they represent:**

- Information needed to make decisions
- Views/projections of data
- Query results

**When to use:**

- When a command needs information
- When an actor needs to see something before deciding
- For dashboards and reports

**Examples:**

- ✅ "Order Summary"
- ✅ "Customer History"
- ✅ "Product Catalog"
- ✅ "Available Inventory"

### Policies (Purple/Lilac) 🟪

**What they represent:**

- Business rules that react to events
- Automatic triggers
- "When X, then Y" logic

**Naming convention:**

- "When [event], [action]" format
- Or just the policy name

**Examples:**

- ✅ "When Order Placed, Reserve Inventory"
- ✅ "When Payment Received, Notify Customer"
- ✅ "Fraud Check Policy"
- ✅ "Auto-Cancel After 24 Hours"

**Common patterns:**

- Event → Policy → Command → Event
- Event → Policy → External System Call

### External Systems (Pink) 🟫

**What they represent:**

- Third-party integrations
- Legacy systems
- Services outside our control

**Examples:**

- ✅ "Payment Gateway"
- ✅ "Email Service"
- ✅ "Legacy ERP"
- ✅ "Tax Calculator API"

**What to capture:**

- Name of the system
- What we send to it
- What we receive back
- Failure modes

### Hot Spots (Red/Hot Pink) ❗

**What they represent:**

- Areas of confusion
- Conflicts between stakeholders
- Missing information
- Things to discuss later

**Types:**

- **Questions** - Need more information
- **Conflicts** - Stakeholders disagree
- **Risks** - Potential problems
- **TODOs** - Need investigation

**Examples:**

- ❗ "Who owns this process?"
- ❗ "What if payment and shipping conflict?"
- ❗ "Need to clarify cancellation rules"
- ❗ "Performance concern here"

## Spatial Layout

### Timeline Flow

Events flow left to right chronologically:

```text
[Start Event] → [Event 2] → [Event 3] → [End Event]
     ↑              ↑           ↑
  [Command]     [Policy]    [Command]
     ↑
  [Actor]
```

### Swimlanes

Group by bounded context or aggregate:

```text
┌─────────────────────────────────────────────────┐
│ Ordering Context                                │
│ [Event] → [Event] → [Event]                     │
└─────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────┐
│ Shipping Context                                │
│ [Event] → [Event] → [Event]                     │
└─────────────────────────────────────────────────┘
```

## Text Representation

When representing event storms in text/markdown:

```markdown
## Event Timeline

### Happy Path

1. 🟧 **Order Placed** [Domain Expert]
   - 🟦 Place Order ← 🟨 Customer
   - → 🟨 Order aggregate

2. 🟪 **Policy: Reserve Inventory**
   - Triggered by: Order Placed
   - Action: Reserve items

3. 🟧 **Inventory Reserved** [Developer]
   - 🟫 Inventory System

4. 🟧 **Order Confirmed** [Domain Expert]
   - 🟩 Order Confirmation (email template)
```

---

**Related:** `workshop-facilitation.md`, `bounded-context-discovery.md`
