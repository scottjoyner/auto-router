# P0 #37 — real strict-live authentication and receipt boundary (2026-10-11)

**Status: runtime characterized in isolated tests; per-human authorization NOT implemented.
Do not merge, deploy or claim real two-user provenance.**

Issue: [auto-router #37](https://github.com/scottjoyner/auto-router/issues/37). Draft stack:
[#35](https://github.com/scottjoyner/auto-router/pull/35) →
[#36](https://github.com/scottjoyner/auto-router/pull/36) →
[#42](https://github.com/scottjoyner/auto-router/pull/42) →
[#45](https://github.com/scottjoyner/auto-router/pull/45).

## Read-only code and executable characterization

Observed `auto_router.secure_live` installs
`install_request_idempotency`, `install_stream_lifecycle`, then
`install_executor_inference_auth`. Starlette places the last-added middleware
outermost, so the current executor auth layer runs before request idempotency.
These are **existing production-code paths**, not new behavior in PR #45.

`ExecutorInferenceAuthMiddleware` accepts one of two routes when enforcement
is on:

- Internal service token: matching `AUTO_ROUTER_INTERNAL_SERVICE_TOKEN`
  places `assistx_service = {identity: assistx-internal, authenticated: true}`
  into request metadata. It is **service authentication**, not a verified
  human-subject claim. Arbitrary client-supplied `user`, `subject_id`,
  `tenant_id` or `root_request_id` in body/metadata are not reclassified as
  trusted human attribution.
- Signed Ed25519 AssistX executor JWT: enforces issuer/audience, task/claim/
  agent, runtime projection generation, scope, allowed model and budget.
  It injects `assistx_executor` lineage with task/claim/agent and an in-memory
  per-JTI attempt counter, but **does not bind tenant and human subject**.
  The in-memory attempt counter is not proof of crash-durable per-human attempts.

`RequestIdempotencyMiddleware` derives the explicit-key namespace from the
bearer-token SHA-256 fingerprint. A shared service bearer is one namespace,
regardless of two human identities asserted in request JSON. For separate
human actors using the same shared service bearer and `Idempotency-Key`,
the second request can receive 409, rather than receiving an independent
per-human namespace. This behavior is expected from the current middleware
and must not be mislabeled as verified actor isolation.

`stream_lifecycle.py` correctly sets
`authenticated_claim_binding=false`, `claim_id=null`, and
`instance_identity_source=configured_route_not_runtime_attestation` for
its affected outbox events. Do not promote those fields until independently
verified source binding exists. Forged metadata must not become signed evidence.

Executable evidence:
`tests/test_p0_37_real_auth_boundary.py` uses actual
`ExecutorInferenceAuthMiddleware` and `RequestIdempotencyMiddleware` in
isolated local ASGI/TestClient harnesses with disposable keys, token and SQLite.
Three tests passed on x1-370; no live router, real user, inference endpoint,
production signing keys or trace store was touched. Passing tests describe an
**OPEN** capability gap, not its closure.

## Smallest safe cross-component contract

The first real per-user slice should be a **disabled-by-default shadow ingress**
rather than modifying admission, selected providers or existing traces.

1. A trusted ingress gateway validates two genuine, independently authenticated
   human principals and the exact tenant/subject mapping. Direct calls and
   shared service tokens are **UNKNOWN HUMAN**, not implicitly Scott/Alex/Alice/Bob.
2. The gateway mints a unique request root and signed attempt ID **for each**
   forwarded provider attempt, including retry attempts on the same verified
   owner-bound root. Include audience, issuer, signing key ID, tenant, subject,
   authorized model handle, issued/expiry times and an idempotent event ID.
   Authorize server-to-server independently of request body, `user`,
   `traceparent`, IP address, client trace ID or caller `metadata`.
3. Router shadow verifier (separate trusted key and audience from executor JWT)
   checks signature, time window, issuer/audience, model scope and replay
   before recording attribution; rejects unknown issuer, unauthorized model,
   expired new attempts, changed retry owner and duplicate conflicting event.
   Preserve the current executor-admission boundary; do not silently upgrade
   or replace AssistX claim auth.
4. **Only a verified per-human principal** may scope actor-owned idempotency,
   immutable attempt receipts and readback. The existing bearer-fingerprint
   idempotency namespace stays intact for legacy service calls.
   Never infer ownership from a shared bearer or caller-supplied user label.
5. Add a trusted provider-attempt observer. An accepted HTTP request, one
   network byte, the streaming protocol terminal, a configured model node,
   and a loaded-instance inventory ID are separate evidence classes.
   Actual process execution needs independently correlated provider-level
   attempt evidence, not stream `[DONE]` or a signed *synthetic* fixture.
6. Custody stays disabled until the approved encrypted trace sink implements
   tenant+subject+producer+node read, subscription and replay authorization.
   Delayed offline replay must distinguish a pre-expiry signed event from a
   fresh request using an expired delegation.

## Required acceptance before a real two-user test

| Gate | Today | Required source evidence |
| --- | --- | --- |
| Service/executor JWT validation | Existing | Do not reinterpret as per-human |
| Two real human subjects | **OPEN** | Gateway identity provider, separately authenticated A/B |
| Server-minted owner/root/attempt | Synthetic only | Validated signed delegation at actual ingress |
| Same shared bearer A/B idempotency | **Collides by design** | Verified actor namespace, explicit retry separation |
| Process/model execution witness | **OPEN** | Independently observed real provider/attempt/process |
| Response delivery | UNKNOWN | Client/transport-specific delivery receipt |
| Offline encrypted custody and actor read | **OPEN** | Audited sink + encrypted spool + read enforcement |
| Safe resident physical candidate | **OPEN** | Capacity approval and independent provider identity |
| Beelink inference | **NO_GO** | Root filesystem and I/O remediation, separately approved |
| Complete physical A/B proof | **OPEN** | Overlapping prompt/collision/retry/cancel/offline proof |

The Beelink root filesystem has approximately 3 GiB unprivileged free and
substantial I/O stalls. It hosts ongoing NAS recovery workloads and cannot
be selected for a physical proof merely because LM Studio lists a resident
model. See [Beelink pressure evidence](P0_37_BEELINK_HOST_PRESSURE_NO_GO_20261011.md).

**Boundary:** The test-only additions and this specification do not alter
`secure_live`, `executor_auth`, `request_idempotency`, route selection,
model residency, live services, approved event outbox or AssistX custody.
All relevant PRs remain draft pending actual ingress integration and verified
physical/custody evidence.
