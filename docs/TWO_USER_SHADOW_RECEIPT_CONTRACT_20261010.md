# Issue #37 — isolated two-user receipt contract (2026-10-10)

Status: **TEST-ONLY EXPERIMENT; NO PRODUCTION INGRESS, ROUTING, AUTH OR TRACE CUSTODY CHANGE.**

Base: draft auto-router PR #36 (stacked on draft PR #35). Source of truth: [P0 #37](https://github.com/scottjoyner/auto-router/issues/37).

## Purpose and minimum boundary

Exercise **two independently authenticated test subjects within the same tenant**
against the same *configured* model, with overlapping requests, deliberately
colliding client trace IDs, adversarial client provenance, terminal-stream
evidence, actor-scoped reads and idempotent offline replay.

This is a **contract simulation**, NOT a physical two-user LM Studio or
llama.cpp canary. Two disposable Ed25519 keys authenticate users in the test;
a separate disposable gateway key signs short-lived per-attempt delegations.
The independent private keys demonstrate non-interchangeable fixture identities
but DO NOT prove a production identity provider or human-user integration.

Nothing binds this test to secure_live authentication middleware. Do not
interpret the fixture as a deployed, trusted ingress feature.

## Implemented fixture-only guarantees

- A per-user public-key challenge is single-use, short-lived and subject-bound;
  the sender cannot authenticate one principal with the other principal's key.
- The gateway resolves the owner from a server-held session; creates UUID
  request roots and distinct UUID attempt IDs; authorizes a model handle;
  refuses cross-owner retry roots; signs a 60-second delegated test envelope.
- A separate in-memory shadow ledger verifies the signed envelope before
  creating a receipt; it does not accept owner, node, claim, model-instance or
  request root from client JSON or a W3C trace header.
- Readback always scopes by *tenant and subject*. Two users on one tenant,
  node and model cannot access each other's receipts even when trace IDs
  collide. This is stricter than tenant-only readback.
- A replayed identical attempt is idempotent; the same signed attempt with
  a conflicting terminal result is rejected. Retry uses a fresh attempt ID
  while retaining its verified owner's root.
- The **real PR #35/#36 StreamUsageCollector** processes fixture SSE bytes.
  Clean HTTP EOF without recognized terminal is incomplete /
  eof_without_terminal, not completed. Responses failed/incomplete
  cannot be upgraded by later [DONE]. Cancellation and transport
  exceptions are distinct from protocol completion.
- No prompt text, response text, credentials or untrusted exception strings
  enter receipts. Client-supplied trace ID, when bounded, contributes only
  a short fingerprint. response_delivered remains unknown;
  provider_execution_attested is **always false**.

### Running the disposable suite

From a development checkout on the draft stack with pytest and the existing
repo runtime dependencies installed:

    pytest -q tests/test_two_user_shadow_receipts.py

The test imports auto_router.stream_lifecycle.StreamUsageCollector; do not
substitute a handwritten terminal-marker parser for the actual implementation.
No network requests, LM Studio process, router DB, NAS path, AssistX writer,
port listener, model load or production credential is needed.

## Acceptance to record

| Gate | Fixture proof | Production release |
| --- | --- | --- |
| Separate A/B cryptographic test auth | In tests | **OPEN** (real IdP/gateway) |
| Same-tenant actor isolation | In memory | **OPEN** (approved trace sink) |
| Server-minted distinct root + attempt | In tests | **OPEN** |
| Incomplete vs completed SSE | Real parser with synthetic streams | **OPEN** (live observation) |
| Replay conflict / owner protection | In memory | **OPEN** (encrypted offline spool) |
| Selected route vs attested process | Explicitly separated | **OPEN** (provider attestation) |
| Physical concurrent LM Studio / llama.cpp | Not attempted | **OPEN** |
| Client delivery / readback custody | Explicit unknown / disposable | **OPEN** |
| p50/p95/p99 + bytes and privacy overhead | Not measured | **OPEN** |

### Required physical canary prerequisites

1. Operator approves a bounded experiment window, capacity, and an
   independently observed **existing resident** model; do not autoload.
   The issue's 2026-10-10 environment comment observed zero loaded_instances
   on x1-370 at that time. Recheck before any actual run.
2. Trusted production gateway authenticates two distinct real subjects and
   passes a verifiable per-request delegation to the router, not a static
   shared service token. Unsupported/direct provider calls are UNKNOWN owner.
3. A router/server observer can correlate **provider-confirmed** process/model
   evidence with the request attempt. [DONE] proves a protocol terminal
   observation, not GPU execution by a specific process or delivery to iOS.
4. Trace sink enforces intersection of tenant, subject, producer and node
   for every read/subscription/replay. The AssistX PR #281 tenant/node/producer
   draft must NOT be treated as per-human separation unless actor policy
   is independently proven.
5. Run A/B overlapping tiny synthetic prompts with same client trace ID;
   test EOF-without-terminal, cancellation, retry, offline disconnect, and
   no direct-client bypass claims. Record exact SHAs, capacity, byte overhead,
   and observations before considering any release gate.

## Explicitly out of scope

No changes to auto_router.secure_live, selected provider, route planner,
request idempotency DB, outbox, AssistX intake, signing material, privacy
flags, physical instance state, UI, or model loading. The experiment does not
solve all of issue #37 and must not promote Kipnerter 1.7 trace claims.
