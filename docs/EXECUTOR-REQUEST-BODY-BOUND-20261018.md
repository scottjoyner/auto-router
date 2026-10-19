# Auto-Router | bounded request buffering before executor-token validation

October 18, 2026. Research-only source hardening, NOT deployed.

## Reason

The strict inference ExecutorInferenceAuthMiddleware reads the complete
request body before checking the Ed25519 executor token. Its previous
_read_body loop extended an unbounded bytearray on every ASGI request
chunk. A large anonymous request could therefore consume memory before
JWT verification or runtime admission.

## Patch

- Bound buffered request bodies to 8 MiB. Check chunk length BEFORE
  appending it; oversized content returns HTTP 413 before model dispatch.
- Handle both a single oversized request frame and streamed/multipart
  ASGI body chunks crossing the limit.
- Deny nonbytes ASGI body data.
- Preserve the existing replay of valid parsed requests to downstream
  routes and normal signed-token verification.
- No model calls, passwords, telemetry writes or routing changes in
  the new unit tests.

The 8MiB value is a conservative temporary API-contract decision, NOT
proof it fits every 262K-context or multimodal client. Before production
deployment, profile legitimate maximum requests, document the gateway's
accepted payload envelope, and coordinate edge/load-balancer body
limits. This branch does not change production defaults.

## Validation

Focused executor-auth, claim-fence, request-idempotency tests: 17 passed.
Full nonintegration suite on this origin/main-based branch:
398 passed, 9 skipped, 1 failed. The sole failure is the existing live
registry-drift gate for x1 LM Studio model toolcall-v5-3b-combined-r2,
also reproduced in a separate unmodified auto-router checkout.
The drift gate remains untouched and must not be silenced to pass CI.

## Remaining production security gates

- AssistX does not yet provide a canonical human owner_id through the
  inspected JWT issuer and claim-status response. Task/claim/agent
  matching cannot substitute for two authenticated human principals.
- The executor attempt ledger is process-local and does not prove
  replay resistance across concurrent router workers or restarts.
- Independent service listener restrictions, current signed runtime
  authority, cancellation, terminal receipts, rollback, and 72-hour
  soak remain unverified for the proposed Lenovo degraded fallback.

GLM-5.3 production admission and automatic Lenovo fallback remain HOLD.
