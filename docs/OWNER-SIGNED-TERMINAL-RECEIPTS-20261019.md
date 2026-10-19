# Owner-bound signed terminal receipt custody — research contract

October 19, 2026. **Review-only stacked follow-up to Auto-Router draft PR #73
and underlying owner-fence draft PR #72.** The AssistX authoritative Task
owner producer is draft PR #413. No live router, gateway, signer, database,
credential, model, or fleet admission configuration was changed.

## What this contract proves — and what it does not

A production-grade inference completion must not be confused with an
HTTP 200, a one-token local test, a process-local route-event counter,
or a signed attempt reservation. A *separately controlled* receipt issuer
must attest a real terminal outcome after validating the task owner,
claim, model, runtime and current fencing epoch.

The new `auto_router.terminal_receipts` module is a **local consumer
prototype** for the resulting externally signed observation. It does
NOT issue signing keys, sign receipts, observe a live terminal stream,
authorize any result, refresh leases, own execution admissions, or
provide an authenticated client endpoint. Do not enable it as a
production route or interpret test signatures as externally attested.

The receipt payload is canonical JSON, Ed25519-verified using an
explicit pinned public key and signer ID. The signed payload binds:

- Unique request ID and receipt ID (within a small bounded schema).
- Domain-separated hashed owner issuer/subject, hashed task/claim/agent
  and projection-generation tuple, and hashed signed executor JWT JTI.
- Attempt number, projection generation, model ID, runtime instance,
  and a *fresh independently established* fencing epoch.
- Terminal outcome: completed, failed, cancelled or possibly_accepted.
- A completed outcome requires a SHA-256 output digest and positive
  completion-token count; failure, cancellation and uncertain acceptance
  never carry a success/output attestation.
- Bounded event timestamp (<=300s of separately supplied current time)
  to refuse stale/future receipt claims. This alone is not a
  rollback-resistant monotonic clock.

An independent trusted caller MUST provide its authenticated owner,
active claim, model/runtime and current *external* fencing epoch.
This package compares them to signed fields; it **cannot** establish
those authorities or their freshness by itself. It must not accept
owner identity from HTTP headers, request JSON or self-declared metadata.

## Single-host durable custody behavior

An explicitly configured private, file-backed SQLite store records
the full signed payload and signature plus hashed lookup indices.
BEGIN IMMEDIATE and FULL synchronous durability ensure one local
transaction reserves a request's *single immutable terminal outcome*.
Exact repeat delivery is idempotent and reports duplicate.
A conflicting outcome, changed signer, different owner, model, claim
or fencing epoch, or duplicate receipt ID for another request is denied.
An uncertain possibly_accepted outcome never silently upgrades into a
completed result. Verified receipts survive interpreter/worker restart.

Private path/0700 parent/0600 database rules are inherited from the
existing owner-attempt prototype. Raw owner IDs and JWT JTI strings are
not stored in the receipt database. Digesting low-entropy identifiers
is **not encryption or complete privacy protection**; production should
apply appropriate access controls and cryptographic custody of keys.

Read-back re-verifies the signed payload and that indexed row fields
still match. It rejects corrupted or tampered stored results.

## Local test validation

42 dedicated terminal-receipt tests, plus adjacent owner/attempt/JWT/
claim-fence suites, passed: **109 focused passing**. Tests include:

- Ephemeral Ed25519 signer and public-key-only verification, invalid
  signature/signer/domain/encoding and incorrect output semantics.
- Swapped owner, agent, task, claim, JTI, projection generation, attempt,
  model, and stale fencing epoch denied against an independently supplied
  expected binding.
- Exact duplicate and conflicting double-delivery/receipt ID refusal;
  24-way SQLite duplicate race admits exactly one immutable result.
- Two different valid signatures for the same request cannot both
  commit incompatible terminal results.
- Fresh process reads and validates a signed persisted completion.
- Distinct signed cancellation/uncertain/failed outcomes are not
  silently promoted to completed.
- Missing/unsafe/symlinked storage, row-index tampering, missing owner
  or trusted epoch and stale clock denial.

This is **not** actual physical multi-user model execution, independent
signer custody, a gateway-authenticated receipt callback, or a trusted
global lease-generation witness. It does not validate terminal
generation quality or reconstruct completions from hashes.

## Required production blockers

1. Real trusted two-principal gateway login and strict Task ownership
   (AssistX #413, Auto-Router #72).
2. Independent resource-side terminal-event producer, signed at a
   custody boundary separate from untrusted request callers and
   independently checked against actual observed stream completion.
3. Source-authoritative live claim/revocation check and a single
   fencing epoch coordinator. The supplied expected binding is a
   dependency, NOT implemented by this prototype.
4. **Global** durable authority across router hosts. SQLite on a
   local filesystem does not fence x1/xwing concurrent leaders, power
   loss of the only writer, replayed disk images, signer key compromise
   or simultaneous failover.
5. Crash-injection, disk-full, tamper/replay, independent key rotation,
   actual signed terminal receipts from two authenticated users,
   cross-host competition, 72-hour loaded serving soak and safe
   rollback. Signer compromise needs hard revocation and key history.
6. Lenovo LFM 1.2B only for an expressly accepted reduced-capability
   task set; never silently substitute it for GLM-5.3 321B.

**Decision: REVIEW ONLY / NO PROD DEPLOY / NO AUTO FAILOVER.**

## Broad regression evidence (same physical test window)

Auto-Router full non-integration suite: **495 passed, 9 skipped,
1 live drift failure**. The failure is the already-known
ProviderRegistryDriftTests.test_registry_matches_live_hosts check;
x1-370's advertised toolcall-v5-3b-combined-r2 alias was absent
from its observed served model catalog. This issue was independently
seen in earlier unmodified router checkouts. The failure is preserved,
not skipped or altered to make this PR green. It is not a failure in
the signed terminal receipt tests.

## Explicit local split-brain counterexample

A further negative architecture test proves that two *independent*
SQLite databases, representing router hosts without common storage,
will **both** record the same perfectly signed receipt as new. The
test intentionally PASSES only by demonstrating duplicate acceptance:
there is no cross-host global election, shared transaction state or
fencing witness implemented here. A shared authoritative store must
be made mandatory BEFORE any production receipt admission.
