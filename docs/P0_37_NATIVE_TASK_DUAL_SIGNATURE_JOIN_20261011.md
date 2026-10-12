# P0 #37 / #61 — Verify-only native-task receipt join

**Evidence checkpoint:** October 11, 2026 (EDT).  
**State:** **RESEARCH / DRAFT / NOT DEPLOYED / NO-GO**.  
**Parent stack:** [Auto-router draft #62](https://github.com/scottjoyner/auto-router/pull/62) (physical process/file preflight) → [draft #60](https://github.com/scottjoyner/auto-router/pull/60) (signed node-observer claims) → [draft #51](https://github.com/scottjoyner/auto-router/pull/51) (signed-user v2 and durable PostgreSQL ownership).  
**Companion native source:** [llama.cpp draft PR #1](https://github.com/scottjoyner/llama.cpp/pull/1) / [branch pinned to xwing Prism source](https://github.com/scottjoyner/llama.cpp/tree/experiment/p0-prism-2459f68b-native-tap-20261011).  
**Track:** [Auto-router P0 #37](https://github.com/scottjoyner/auto-router/issues/37) and [physical process witness #61](https://github.com/scottjoyner/auto-router/issues/61).

## Why another boundary is required

The gateway's signed delegation binds the exact canonical request body
and `root_request_id`/`attempt_id` to a verified test subject, but the
native llama.cpp C++ code assigns **process-local integer task IDs**.

A signed node observer cannot truthfully attribute a task to Alice
solely from a client-supplied `user`, `traceparent`,
`X-Request-ID` or `X-Root-ID` header. Existing request-origin
metadata can be forged or copied. A native task can be recycled after
a server restart and does not itself prove user authorization.

The narrow experimental component
`src/auto_router/native_task_receipt_join.py` validates a
**two-signer, immutable evidence bundle** without adding any actual
runtime ingress signer, native sensor, network endpoint, model load,
database migration, or trace write. The function is *pure verify-only*
and cannot promote an observation to physical proof.

### Signed native admission receipt (unimplemented real producer)

Signed only by the **separately pinned post-auth native-ingress
Ed25519 key** with domain `p0-native-admitted-task-v1\\0`:

- Fixed issuer, audience, schema, pinned ingress key ID;
- Signed original request root UUIDv4, attempt UUIDv4, exact endpoint
  `/v1/chat/completions`, canonical original request SHA256,
  authorized model handle;
- Operator-pinned node ID, runtime instance ID, boot UUID, OS PID and
  positive process-start ticks, independently measured model artifact
  SHA256 (currently only an unverified synthetic input);
- Native task ID, slot ID, unique admission nonce and bounded
  observation timestamp.

The signed receipt is checked against the **already verified v2
gateway-delegation actor**. A production caller must obtain that actor
from the authenticated router ASGI scope; a dictionary constructed from
client JSON or a shared service bearer is not authorization.

The *real native ingress receipt producer does not exist yet*.
The verifier alone does not make the server parse/verify gateway
delegation, forward a trusted claim, or sign a native task ID.

### Independently signed native lifecycle (unimplemented real producer)

An **independent** Ed25519 public key signs domain-separated events
with the same bound boot/PID/start ticks, node, runtime, model artifact,
native task ID and slot ID. Each event additionally signs the
**SHA256 hash of the exact signed ingress payload** under a separate
domain, a monotonic sequence, UUIDv4 event ID, event timestamp,
generated-token count and stage:

```text
slot_assigned
  -> (sampled_token){0..6}
  -> final_result_enqueued | native_error | native_cancelled
```

The maximum proof bundle contains **8** events; bounded events are a
contract for an operator-approved summary, *not a token-by-token
stream recorder*. The C++ fixture currently emits every sampled token
when enabled; a later approved collector must compact that high-rate
stream asynchronously without blocking model inference or silently
inventing terminal events. `final_result_enqueued` means an internal
native result was enqueued, not that the client received the response.
A cancelled or errored task could take other C++ code paths that are
not yet instrumented. The C++ fixture only carries task/slot/token
count; **it does not actually sign these Python evidence records**.

**Key separation:** ingress and native-observer public keys are
explicitly required to differ. Distinct key bytes **do not prove**
independent key custody or trustworthy sensors; those require
separately audited node deployments, capability isolation and
operational evidence.

### What this verifier rejects

- Wrong gateway actor, root, attempt, request endpoint or prompt digest;
- Different model, node, boot, process ID/start ticks, GGUF digest,
  native task/slot ID, runtime instance or swapped admission nonce;
- Wrong signer, identical ingress/observer keys, key ID, audience,
  domain, replayed event UUID, duplicate/noncanonical signed JSON;
- Missing native admission receipt, malformed sequence, stale/future
  event, too many records, token counter regression, impossible
  lifecycle or events after a terminal outcome;
- **Distinct independently verified synthetic human** with the same
  model/prompt/tenant, and **new signed retry attempt** under the same
  root trying to reuse the first attempt's native receipt.

### Explicit limits and output semantics

Even with a completely valid signed test bundle:

```json
{
  "admission_correlation": "signed_fixture_ingress_correlated",
  "native_lifecycle_correlation": "signed_fixture_terminal_claim",
  "native_terminal_claim_correlated": true,
  "provider_execution_attested": false,
  "physical_provider_proof": false,
  "first_token_proven": false,
  "response_delivered": "unknown",
  "encrypted_trace_custody_verified": false,
  "cross_bundle_replay_verified": false,
  "operational_signer_provenance_verified": false
}
```

A signature authenticates **bytes and key custody**, not the honesty
of a native sensor. Signed fixtures can still be fabricated.
Cross-bundle nonce replay is not prevented by this stateless checker:
that requires a separate **durable, single-primary, owner-bound
replay ledger**, with fencing and revocation. The verifier deliberately
does not return tenant/subject or raw source payloads. Native model
artifact SHA is a syntactically checked claim, not a GGUF digest
measured by the current tool. Unknown client delivery remains unknown.

There is **no codepath setting `provider_execution_attested=true`**;
no execution or release admission flag exists in the component.

## Exact test scope

Run on an isolated checkout with the existing router dev environment:

```sh
ruff check src/auto_router/native_task_receipt_join.py \
  tests/test_p0_37_native_task_receipt_join.py

python -m pytest -q --disable-warnings \
  tests/test_p0_37_native_task_receipt_join.py

python -m pytest -q --disable-warnings \
  -k 'not test_registry_matches_live_hosts'
```

At implementation SHA `d45b6faa28dba43a339cac1b8364768f5811637c`
on x1-370: **67 focused tests PASS**, Ruff **PASS**; broader
isolated fixture regression **692 passed, 27 skipped, 1 deselected**.
The exclusion is the previously independently failing
live-provider-registry check; optional real PostgreSQL/AssistX
integration fixtures are skipped, not accepted. A new isolated hosted
workflow tests only the join module against **fictional, ephemeral
Ed25519 keys**, without real provider access, DB, or secrets.

## Future physical acceptance; do not skip

1. Operator approves protected native ingress identity, post-auth
   delegation verification and a **bounded root/attempt→native task
   assignment** receipt whose signer/nonce cannot be controlled by an
   unauthenticated client.
2. Independently attested native process observer with stable
   boot/PID/start ticks, verified **full** GGUF digest/build provenance,
   a nonblocking bounded producer path and independently governed
   Ed25519 key; no sensitive prompt/token payloads.
3. Correlation producer is independently tested at actual
   `llama-server` runtime **after** validation of protected native
   residency. Validate error/cancel/partial outcomes; task ID reuse,
   process restart and host reboots; no SSE delivery inference.
4. Durable single-primary anti-replay and fenced shared-ownership
   authority, subject/tenant/producer/node scoped **encrypted** trace
   custody and ACL-filtered readback.
5. Two **real** independently authenticated IdP users, sustained
   healthy host capacity, an explicitly approved bounded physical
   overlapping A/B model request, and signed-device/iPhone delivery
   receipt. The running xwing binary has **not** been rebuilt;
   its protected Bonsai inventory returned unauthenticated 401.
   Beelink remains physical **NO-GO** from storage/I/O pressure.

**No workflow here loads a model, issues physical inference, reads
API keys, writes NAS custody, changes deployment flags or restarts
a running host. Keep this stack DRAFT/unmerged.**
