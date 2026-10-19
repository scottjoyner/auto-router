# Auto-Router | durable owner-bound executor attempt fence

October 19, 2026. **Review-only stacked follow-up to draft PR #72.**
No feature activation, production router deployment or live inference
model routing change. GLM production and automatic Lenovo fallback HOLD.

## Concrete restart-safety gap

Prior ExecutorInferenceAuthMiddleware used _ATTEMPTS, an in-memory
dictionary keyed by signed executor-token JTI. Restarting a worker or
submitting the same JWT to a different router process could restart
the signed max_attempts allowance before JWT expiration.

Router idempotency already has SQLite persistence, but it is not a
substitute for signed executor-token attempt budgeting and per-user
claim binding. The execution receipt/stream-result layer is separate.

## Opt-in strict-owner behavior

When AUTO_ROUTER_REQUIRE_OWNER_BINDING=false (default), the existing
process-local attempt logic is unchanged.

When AUTO_ROUTER_REQUIRE_OWNER_BINDING=true, a successful signed
gateway owner check and fresh projection are followed by an atomic
reservation in a *private file-backed SQLite store* supplied via
AUTO_ROUTER_OWNER_ATTEMPT_DB_PATH. No attempt may be sent downstream
without a committed reservation. The configuration requires:

- Absolute SQLite filename in an existing, privately owned directory
  (mode 0700). The database must be owner-only (mode 0600), regular,
  non-symlinked and single-linked.
- Never accepts in-memory/relative paths; absent or unwriteable store
  returns HTTP 503 and **does not fall back** to the prior in-memory
  budget. Atomic BEGIN IMMEDIATE + FULL synchronous durability.
- Strict JTI reuse binds the signed owner issuer/subject, task, claim,
  agent, projection generation, expiry, and permitted max attempts.
  Reusing a JTI with changed signed identity or limits is rejected.
- The DB stores only domain-separated SHA256 digests of token JTI and
  owner/claim identity, expiration, signed attempt ceiling and count.
  Raw owner strings and JTI are not stored.
- Expired or >24-hour executor tokens are rejected in this research
  mode. Maximum signed attempt budget is capped at 100.
- This is an **attempt reservation**, not a proof that a model produced
  a complete or correct response. A consumed attempt is not refunded
  after a crash, timeout, claim revocation or upstream failure.

SQLite coordinates processes sharing one local filesystem. This
does NOT establish safe cross-node routing among x1, xwing and Lenovo,
which would require a single independently fenced durable authority.
Filesystem rollback, signing-key custody, clock rollback and operator
reset remain excluded from this test's trust boundary.

## Physical test results, October 19

- Focused signed JWT middleware, durable attempts, owner bindings and
  live-claim fence: **67 passed**.
- Full nonintegration router suite: **451 passed, 9 skipped** (before
  the last three middleware integration tests were added; all three
  passed in the focused suite).
- Parallel reservation trial: 24 requests contending for a signed
  seven-attempt budget admitted exactly attempts 1–7, denied 17.
- Reopened DB and a separate Python interpreter observed the same
  count; a replayed signed request was refused.
- Verified missing store 503, expired token, different signed owner,
  same JTI with different claim/limits, symlinked/world-readable file
  and public directory denial. No plaintext owner subjects in SQLite.
- No real JWT signing key, production database or actual model
  invocation was needed for these local deterministic tests.

## Requirements before production enablement

1. Review/approve AssistX Task owner source and signed token contract
   in AssistX PR #413 and Auto-Router owner consumer PR #72.
2. Demonstrate a genuine trusted **two-principal gateway**, not synthetic
   ASGI identity injection or client-provided headers.
3. Establish a **single authoritative** cross-router-worker/node
   transaction store, with encrypted backup, explicit failure mode,
   monotonic/fenced replay high-water, revocation and disk-full
   recovery. A standalone SQLite file on one host is insufficient
   for a horizontally distributed fleet.
4. Record durable *terminal* stream/completion receipts with owner,
   claim, model, projection generation and a verified final status.
   This slice only records pre-dispatch attempt consumption.
5. Verify 72-hour realistic availability, model task compatibility,
   caller access controls, controlled rollback and loaded-model proof.

No automation should enable strict owner mode before the real trusted
gateway, AssistX producer, and a shared production-grade receipt/attempt
authority have passed their independent acceptance tests.
