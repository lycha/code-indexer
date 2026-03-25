# Persona Prompt Templates

## Overview

This document provides prompt templates for invoking each persona agent during event storming simulation.

## Domain Expert Prompts

### Initial Exploration

```markdown
Analyze {domain} from a Subject Matter Expert perspective.

Identify:
1. All domain events (things that happen in the business)
2. Business rules that govern these events
3. Edge cases and exceptions
4. Industry-specific terminology

Format your response as:
- Events (past tense, e.g., "Order Placed")
- Business Rules (constraints and conditions)
- Edge Cases (unusual scenarios)
- Terminology (domain-specific terms)

Mark all contributions with [Domain Expert].
```

### Focused Exploration

```markdown
As the Domain Expert, focus on {specific_area} within {domain}.

What events occur in this area?
What business rules apply?
What terminology is used?
What do newcomers often misunderstand?
```

### Challenge Response

```markdown
The Developer raised a concern: {concern}

As the Domain Expert, respond:
- Is this a real business constraint?
- How does the business handle this today?
- What's the correct domain terminology?
```

## Developer Prompts

### Initial Exploration

```markdown
Analyze {domain} from a Technical Developer perspective.

Identify:
1. Technical events (system-generated, integration, async)
2. Integration points (external APIs, legacy systems)
3. Technical constraints (performance, scalability)
4. Data requirements (what data flows where)

Format your response as:
- Technical Events (system perspective)
- Integration Points (external dependencies)
- Constraints (technical limitations)
- Data Requirements (per event/command)

Mark all contributions with [Developer].
```

### Feasibility Assessment

```markdown
Review these domain events from a technical feasibility perspective:
{events_list}

For each, assess:
- Implementation complexity (Low/Medium/High)
- Integration requirements
- Technical risks
- Missing technical events
```

### Integration Analysis

```markdown
For {domain}, identify all integration points:
- External services and APIs
- Legacy systems
- Third-party dependencies
- Async vs sync requirements
- Error handling patterns
```

## Business Analyst Prompts

### Initial Exploration

```markdown
Analyze {domain} from a Business Analyst perspective.

Identify:
1. Commands (actions that trigger events)
2. Actors (who issues each command)
3. Process flows (sequences of events)
4. Acceptance criteria (how we verify success)

Format your response as:
- Commands → Events (what triggers what)
- Actors (who does what)
- Process Flows (happy path and alternatives)
- Acceptance Criteria (Given/When/Then)

Mark all contributions with [Business Analyst].
```

### Process Mapping

```markdown
Map the complete process flow for {process} in {domain}.

Include:
- Starting trigger
- Each step with actor and command
- Decision points and branches
- Exception handling paths
- End states
```

### Requirements Extraction

```markdown
From these events: {events_list}

Extract user stories in format:
"As a [role], I want to [command] so that [benefit]"

For each story, define acceptance criteria.
```

## Product Owner Prompts

### Initial Exploration

```markdown
Analyze {domain} from a Product Owner perspective.

Identify:
1. High-value features/events
2. MVP scope (must-have vs nice-to-have)
3. Priority ranking of bounded contexts
4. User value for each capability

Format your response as:
- Value Assessment (High/Medium/Low per feature)
- MVP Scope (Must Have / Should Have / Could Have / Won't Have)
- Priority Ranking (ordered list of contexts/features)
- User Stories (high-value stories)

Mark all contributions with [Product Owner].
```

### Prioritization

```markdown
Given these bounded contexts: {contexts_list}

Prioritize for delivery order considering:
- User value
- Technical dependencies
- Risk
- Market timing

Provide rationale for each ranking.
```

### Scope Definition

```markdown
For {feature} in {domain}:

Define MVP scope:
- What's the minimum to deliver value?
- What can be deferred?
- What dependencies exist?
- What's the rollout strategy?
```

## Devil's Advocate Prompts

### Initial Exploration

```markdown
Analyze {domain} as a Critical Challenger (Devil's Advocate).

Identify:
1. Hot spots (areas of confusion or conflict)
2. Missing scenarios (what hasn't been considered)
3. Contradictions (between perspectives)
4. Failure modes (what can go wrong)

Format your response as:
- Hot Spots (conflicts requiring resolution)
- Missing Scenarios (gaps in coverage)
- Contradictions (conflicting requirements)
- Failure Modes (error scenarios)

Mark all contributions with [Devil's Advocate].
```

### Challenge Synthesis

```markdown
Review this event storm synthesis:
{synthesis}

Challenge it:
- What scenarios are missing?
- Where are there contradictions?
- What failure modes aren't addressed?
- What assumptions are being made?
- What's the 1% edge case no one mentioned?
```

### Hot Spot Deep Dive

```markdown
This hot spot was identified: {hot_spot}

Analyze it:
- Why is this contentious?
- What perspectives conflict?
- What's the real underlying issue?
- What questions need answers to resolve it?
```

## Combined Prompt (Quick Mode)

For quick mode without persona agents:

```markdown
Perform a rapid event storming analysis for {domain}.

Cover all perspectives briefly:
1. Domain Events (business perspective)
2. Technical Events (system perspective)
3. Commands and Actors (process perspective)
4. Priorities (product perspective)
5. Gaps and Risks (critical perspective)

Output a consolidated event catalog with bounded contexts.
```

## Synthesis Prompt

After all personas respond:

```markdown
Synthesize these persona outputs into a unified event catalog:

{persona_outputs}

Create:
1. Deduplicated event list with attribution
2. Command → Event mappings
3. Actor → Command mappings
4. Bounded context groupings
5. Hot spot summary with resolution status
```

---

**Related:** `workshop-facilitation.md`, `sticky-note-types.md`
