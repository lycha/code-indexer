# Severity Levels

Use consistent severity labels so the reviewer and author can prioritize fixes quickly.

## Critical (C)
**Definition:** Security vulnerabilities, data corruption, or correctness issues that can cause major user impact, legal exposure, or irreversible damage. Must be fixed before merge.

**Examples:**
- Open redirect that leaks auth tokens
- JWT verification bypass or missing audience/issuer validation
- Transaction that can orphan critical data

## Major (M)
**Definition:** Significant correctness, reliability, or maintainability issues that can cause outages, hard-to-debug errors, or future regressions. Should be addressed before merge or immediately after with a tracked task.

**Examples:**
- Broad `catch (Exception)` hiding real failures
- Missing validation for critical request fields
- Non-idempotent write endpoints without safeguards

## Minor (m)
**Definition:** Style, clarity, or low-risk refactors that improve readability or consistency without blocking merge.

**Examples:**
- Inconsistent naming or duplicate helpers
- Logging clarity or missing context fields
- Small refactors for code reuse
