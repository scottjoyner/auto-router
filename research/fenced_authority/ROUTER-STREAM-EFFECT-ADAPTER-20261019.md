# Default-off Auto-Router stream effect adapter — October 19, 2026

**LOCAL REVIEW ONLY. NEVER ENABLE WITHOUT AN AUTHENTICATED EPOCH BROKER.**

The underlying physical synthetic worker fence ran on xwing and used
a disposable shared PostgreSQL primary. Draft Auto-Router PR #75 is the
latest published GitHub baseline; this continuation is LOCAL ONLY.
No production database, model, route, network ACL, key, process or
service was changed.

## Code changes

New src/auto_router/stream_effect_fence.py is integrated with
src/auto_router/stream_lifecycle.py:

- AUTO_ROUTER_REQUIRE_STREAM_EFFECT_FENCE defaults OFF, preserving
  the existing streaming behavior and record logic.
- Enabled strict mode REQUIRES the strict owner-binding configuration.
  Unrecognized/misspelled true values also enable strict enforcement.
- A trusted *internal* state.stream_effect_authority interface must be
  injected by reviewed trusted router initialization. It must expose
  async preflight, accept_chunk, abort methods. It is never read from
  user HTTP headers, request JSON or metadata. Such a production broker
  is NOT implemented in this branch, so strict mode with current
  deployments will DENY all inference.
- Preflight returns exactly True before contacting any provider.
  Missing, false, broken or timed-out preflight blocks dispatch.
- Each actual provider-stream chunk requires a fresh exactly True
  authority ACK binding a monotonic sequence number and the chunk
  SHA256 digest. Each check has a bounded two-second deadline.
  No chunk is recorded or yielded before its acceptance.
- Missing/revoked/expired/partitioned authority suppresses new
  output, triggers bounded stream teardown, and records an uncertain
  possibly_accepted state, NEVER a terminal success.
- Strict mode denies NONSTREAMING inference before dispatch, because
  it would bypass the per-chunk gate. It must not silently accept
  stream=false or provide a default-off escape path.
- Early client stream close also triggers best-effort teardown.
  Both OpenAI-compatible and agentgateway provider stream iterators
  explicitly close nested response.aiter_raw() before closing the
  HTTP streaming context and client. Nested finally blocks ensure
  the client-close attempt runs even if an inner close fails.

## Evidence

28 focused integration and provider/lifecycle tests pass. They test
authorized delivery, revoked second chunk, network partition, fake
metadata/JSON authority, missing trusted source, owner flag bypass,
nonstream bypass, refused preflight before dispatch, timeout, early
close, misconfigured strict flag, invalid chunk, false/failed abort
ACK and provider HTTP context/client shutdown ordering.

Full Auto-Router nonintegration suite against this isolated checkout:
552 passed, 9 skipped, 1 failed. The existing live x1 provider
registry drift for toolcall-v5-3b-combined-r2 remains and was NOT
suppressed. The previous physical two-host synthetic worker/revocation
trial also passed; its evidence remains in the repository.

## Explicit NO-GO limits

This source is an ADAPTER, not a trusted production authority.
A test object returning True does not prove AssistX user ownership,
active claim, current signed epoch, global lease, model identity or
real runtime effect. The actual trusted broker is absent.

Even an atomic per-chunk database acceptance is not atomic with
socket delivery to a remote client; previously accepted bytes can
arrive after the epoch was revoked. Closing an HTTP stream does not
prove a remote model process or GPU stopped generating. A real
resource-side cancellation/stop/drain witness is still required.

Cross-host replicated authority, independent key custody, replay
high-water anti-rollback, real two-user gateway, resource-side
terminal signer, all possible alternative provider endpoints and
a sustained 72-hour serving soak remain unverified.

GLM-5.3 production admission and automated Lenovo fallback: HOLD.
