# GPU-owner proxy contract — source-only, NO-GO for deployment

**Status (2026-10-16):** a unit-tested reference data-plane guard, not a running process. All four current GPU model listeners remain unchanged on their existing Tailnet ports. `create_gpu_owner_app()` is disconnected from Auto-Router, has no default authority or backend, and returns HTTP 503 on `/health` and unconfigured `/v1/chat/completions`.

## Implemented in `src/auto_router/gpu_owner_proxy.py`

- A strict physical-GPU + model binding requires a literal `http://127.0.0.1:<port>` (or another loopback literal) worker URL. This **checks syntax only**; it cannot establish actual firewall isolation.
- The app must be assembled with independently supplied `OwnerAuthority` and `LocalModelBackend` implementations. Neither a production implementation nor deployable authority key material is supplied. A client-supplied `user`, `metadata`, `tools`, `tool_choice`, model override or streaming request is rejected before any GPU work.
- The authority is required to mint a lease containing trusted tenant, actor, request, model, physical GPU group and positive fencing epoch. This reference class validates structure and exact GPU/model agreement but does not authenticate a signed delegation itself; that belongs to the absent authority implementation.
- A bounded non-streaming `POST /v1/chat/completions` request acquires owner custody, validates current status before dispatch and repeatedly during execution, and checks it again before returning. Revocation, authority failure, cancellation, timeout, model mismatch or missing terminal evidence fail closed and ask the backend adapter to abort work.
- Output is considered complete only when exact model ID, stop terminal marker, nonempty text and positive completion-token usage are present. This is **application-level consistency**, not independently attested GPU residency or trustworthy per-user trace lineage.
- A single proxy instance has a local active-request lock. An external authority must impose real cross-process capacity and durable epoch fencing; this guard alone cannot prevent concurrent requests from other proxies.
- HTTP success is never used as owner attestation. `/health` always returns HTTP 503 / `no_go` in this source-only prototype, and the app does not expose an unauthenticated useful completion route by default.

## Tests and evidence

`tests/test_gpu_owner_proxy.py` verifies default denial; strict loopback binding; mock-delegation rejection; physical-card/model identity; request bounds; spoofed owner metadata rejection; model-terminal validation; authority revocation/partition; backend cancellation/abort requests; client cancellation; timeout; failed lease release; and single-proxy contention. These tests use **fake authority and backend only** and do not reach a real GPU. They are complementary to the disposable Redis tests in `tests/test_gpu_distributed_lease.py`.

## Critical remaining blockers (not solved by this code)

1. **Durable authority and delegation.** Implement and verify externally authenticated short-lived tenant/actor delegation and globally durable per-card fencing epochs. Reject replay, old signing keys, stale generation and lease re-issue; never trust a request JSON `user` or service token alone.
2. **Physical isolation and abort.** Operator must move raw model servers behind private host-local listeners or Unix sockets, prove no direct Tailnet bypass, and implement a GPU-owner backend adapter that can forcibly stop/contain work (including stuck HIP/Vulkan kernels). Calling an abstract `abort()` method is **not** evidence that actual GPU work has ended.
3. **Atomic owner transition.** Authority state, worker generation and externally durable epoch must move together. Lease expiry, Redis state loss, router failover and simultaneous proxy instances must never allow stale effects.
4. **Streaming lifecycle.** `stream: true` is currently rejected. Future implementation must retain fencing through the final model event, cancellation and client disconnect. Distinguish first network chunk from first token and require terminal `[DONE]` / completed evidence.
5. **Two-user custody.** Produce trusted per-user attempt IDs/receipts and enforce tenant+actor scoped readback, collision and offline retry tests; Auto-Router issue #37 remains open.
6. **Release/security gates.** Draft PR #41 still protects effective-provider and inherited admin routes. All four GPU candidates in `config/providers.gpu-canary.CANDIDATE.yaml` remain `enabled: false`.

**Release rule:** do not deploy, expose, or call this prototype as a production inference endpoint. Its default health intentionally fails until concrete authority, backend isolation, cancellation and receipt implementations pass independently witnessed tests.
