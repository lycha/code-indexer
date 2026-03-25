# Kotlin/Spring Review Checklist

## Correctness
- Input validation and null-safety checks
- Error handling returns appropriate HTTP status
- Edge cases covered (empty lists, missing entities, duplicates)

## Security
- JWT validation (issuer, audience, signature, expiry)
- Redirects and cookies validated and secured
- Sensitive data not logged
- Auth filters return precise errors (no broad exception masking)

## Data Integrity
- Transactions defined for multi-step writes
- Idempotency for write endpoints where required
- Flyway migrations reversible and safe for existing data
- JOOQ queries scoped to tenant/user where required

## API Contract Alignment
- OpenAPI spec updated for new endpoints/fields
- Backward compatibility maintained
- Error responses documented and consistent

## Observability
- Logs include correlation IDs
- Metrics annotations applied on public endpoints
- External calls have timeout/latency visibility

## Performance
- Avoid blocking calls in hot paths
- Queries use indexes and avoid N+1 patterns
- Caching used for JWKS/verifiers where appropriate

## Tests
- Unit tests for domain logic and services
- Integration tests for auth and persistence changes
- Contract tests updated if OpenAPI changed
