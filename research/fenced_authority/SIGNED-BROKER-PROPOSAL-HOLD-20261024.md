# Offline independently signed broker input proposal — 2026-10-24

**Research-only, no execution authority, no live service or provider calls.**
This is an incremental negative-control and data-provenance prototype stacked
on draft Auto-Router PR #75. It is not a broker deployment and does not replace
the merged strict-owner inference HOLD in PR #97.

## Scope of the experiment

The offline broker_signed_proposal.py verifier takes a compact canonical
EdDSA executor JWT and a separately signed current-resource-witness envelope.
Its caller supplies two distinct, pinned Ed25519 public verification keys;
no request input or environment flag can select a substitute key.

The verifier checks signed issuer, audience, scope, key ID, short validity,
owner issuer/subject, claim/task/JTI, exact model allowlist, attempt limit,
and token budgets. A separately signed resource witness must match the
executor-token digest, task/owner/claim/JTI, requested model, request ID,
account scope, projection and attempt limit. Signature forgeries, stale
times, wrong tenants and altered claims are denied before proposal creation.

The result contains domain-separated hashes suitable for later inspection
and explicitly sets sql_executed=false, broker_admission_authorized=false,
provider_execution_authorized=false, and provider_calls=0. It returns
NO executable SQL, broker credential or database connection.

## Reproduced replay limitation — NO-GO

A recently issued resource witness stays cryptographically valid even after
a revocation occurring AFTER it was signed, until its short expiry. A
negative test replays the same signed witness inside its lifetime and proves
it still passes cryptographic verification but cannot become an execution
grant: current_revocation_proven=false and
external_monotonic_highwater_proven=false.

Both signing keys are synthetic in tests. Offline tests do not prove actual
trust-root custody, authentic user/Task provenance, independently credentialed
broker identity, cross-node shared state or live resource generation.

## Required next step

The resource must verify current revocation/high-water and atomically gate
the physical model effect at start, fallback, and stream continuation,
separately from issuer JWT and PostgreSQL admission. It must independently
sign and retain redacted terminal receipts including uncertain effects.
The real broker must be prevented from inventing an owner even though
its PostgreSQL function privileges are restricted.

Keep PR #75 DRAFT, knowledge issue #57 OPEN, and provider dispatch HOLD.
