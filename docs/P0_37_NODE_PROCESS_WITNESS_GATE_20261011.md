# P0 #37 — Independent provider-observer correlation contract

**Date:** October 11, 2026  
**Status:** DRAFT, research-only, default unused, not deployed.  
**Dependency:** [Auto-router draft #51](https://github.com/scottjoyner/auto-router/pull/51), [AssistX draft #325](https://github.com/scottjoyner/auto-assist/pull/325)  
**Tracking:** [P0 issue #37](https://github.com/scottjoyner/auto-router/issues/37).

## Exact evidentiary purpose

Identity-bound delegations are now cryptographically tied to the **exact
endpoint and canonical request body**. The shared PostgreSQL owner ledger
then prevents two users or two distinct prompts from claiming the same
request root. This proves *admission identity*, **not the physical execution
of that prompt**. Likewise, a configured route, loaded-model inventory, a
provider's HTTP response, and an OpenAI SSE `[DONE]` marker do not
independently identify the OS model process that executed one attempt.

This experimental module, `src/auto_router/provider_attempt_witness.py`,
implements an **offline, pure, read-only verifier** for the next boundary.
It adds **no API route**, middleware installation, provider call, model
autoload, trace write, log forwarding, database migration or admission
switch. The code never signs observations: the *test fixtures only* own a
synthetic Ed25519 signing key.

It accepts an actor obtained only from the existing cryptographically
verified **v2 gateway-delegation middleware**, then compares that actor
against a sequence of detached, domain-separated **node-observer signed**
event objects using a distinct, operator-pinned observer Ed25519 public key.

## Required observer fields and provenance

Each canonical, bounded node event includes:

- Fixed schema, observer issuer, audience, and pinned key ID.
- Root UUIDv4, attempt UUIDv4, exact `/v1/chat/completions` request path,
  original v2 canonical request SHA-256 and authorized model handle.
- Pinned node ID, runtime instance identifier, OS boot UUID, PID and
  `/proc/<pid>/stat`-style process start ticks.
- Full SHA-256 **model-artifact identity** and independently obtained
  *native provider request ID*, stable for one attempt.
- Unique signed event UUID, monotonic sequence, bounded wall-clock
  observation time and explicit lifecycle stage.

Supported stages, in order, are:

1. `resident_snapshot`: observation of this native runtime instance and
   resident model artifact.
2. `attempt_received`: the same process received this native attempt.
3. Optional `native_first_token`: only if the actual provider exposes a
   per-attempt token emission event. A first HTTP network chunk is **not**
   the model's first token.
4. `attempt_terminal`: native completed, failed, cancelled or incomplete.

All stage events must bind to **one** node, boot, OS process start,
runtime/model-artifact identity and native provider request identifier.
Missing stages, stale events, signature mismatch, duplicate fields,
duplicate event IDs, process switching, route/model/prompt mismatch,
malformed terminal disposition and timestamp/order inconsistencies
fail closed. A partial event sequence is reported as partial.

The verifier does **not** accept unsigned client trace IDs, caller JSON
metadata, an inventory listing, the gateway's service bearer, or a
configured provider alias as evidence of the above.

## Explicit no-promotion rule

Even a complete, independently signed *fixture* sequence returns:

```json
{
  "observer_correlation": "signed_terminal_correlated",
  "native_attempt_terminal_observed": true,
  "first_token_proven": false,
  "provider_execution_attested": false,
  "physical_provider_proof": false,
  "response_delivered": "unknown",
  "unverified_source_limitation": "signed_observer_not_independent_process_proof"
}
```

Here `native_attempt_terminal_observed` means a **signed observer
assertion** was structurally correlated, *not* that physical native
instrumentation has been independently vetted. The API deliberately has
**no `promote=True` switch**. A signer controlling the observer private
key could fabricate a perfectly valid sequence; cryptographic validity
proves key custody, not sensor honesty. The fixture signer lives **only in
tests**, never in the runtime verifier module.

The following remains required before any separate production evidence
promotion component could truthfully call an attempt `executed`:

- Approved per-node isolated observer deployment with independent identity
  and key custody, signed boot/process attestation and anti-rollback/epoch
  records.
- Read-only process + model artifact identity measured on the *real node*,
  corroborated with an authenticated native resident-model API.
- A native provider hook or trusted instrumented proxy able to prove
  **per-attempt request arrival** and terminal status for the actual
  PID/model, not merely the router's own forward attempt.
- Operator-approved node/producer/tenant/subject encrypted trace custody,
  owner-scoped read/subscription authorization, offline replay and
  revocation policies. A raw canonical request hash is not a plaintext
  prompt but still requires sensitive trace handling.
- Two **real** independently authenticated users, overlapping physical
  requests to one resident instance, signed terminal outcome, explicit
  unknown client delivery on disconnect and exact iPhone/TestFlight
  build/device traceability.

## Observed physical-node information and limitation

A read-only x1-370 inspection found port `1234` listening and LM Studio
runtime-related processes, but the Fleet Commander isolated HOME caused
`lms ps` to refuse with a missing `lms-key-2` file. This is **not** a
reason to bypass the authentication boundary or switch HOME to another
identity. The host observation was not a native per-attempt witness.
Another read-only registry-drift test previously found configured
`qwen3.5-0.8b-claude-4.6-opus-reasoning-distilled` absent from the
x1-370 server's served-model inventory; do not autoload or waive the
registry check on the basis of this work.

Beelink host-pressure remains physical **NO_GO** until separately
measured/reapproved. Neither this branch nor its tests issue inference
requests or touch production NAS custody.

## Reproducible tests

Run on a disposable checkout of this branch, using Python 3.12:

```sh
ruff check src/auto_router/provider_attempt_witness.py \
  tests/test_p0_37_provider_attempt_witness.py

python -m pytest -q tests/test_p0_37_provider_attempt_witness.py
```

The adversarial tests use **two distinct fixture Ed25519 signing keys**
(gateway actor and node observer) and verify both valid correlation and
rejection of wrong actor/prompt, model, node, runtime instance, boot,
process start, model artifact, native request ID, stale/reordered events,
repeated event identities, duplicate signed JSON and wrong signing
domain. No physical LM Studio process is attested.

This is a safe **component contract**, not P0 release acceptance.
