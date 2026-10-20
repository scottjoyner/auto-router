# Authenticated owner fence — opt-in, deny-only consumer

October 18, 2026. RESEARCH / NO DEPLOY / NO PRODUCTION AUTH CLAIMS.

## Source-contract findings

The existing AssistX /api/tasks endpoint verifies an authenticated caller
but discards that caller identity before persisting a Task. Task upserts
use a global idempotency key that can update previously stored properties.
Neither behavior proves human or tenant ownership.

AssistX signed executor JWT and live claim-status responses currently
bind task_id, claim_id, agent_id, projection_generation and lease. These
are valuable task-execution boundaries but not human owner bindings.
AssistX also accepts a configurable trusted-auth header: before
elevating that data, prove the reverse proxy strips all untrusted client
copies. The default Basic account cannot represent multiple human users.

## Isolated Auto-Router patch

- Explicit feature flag AUTO_ROUTER_REQUIRE_OWNER_BINDING defaults OFF.
- When enabled, require a verified authenticated principal injected
  internally into ASGI scope.state.verified_owner, not headers or JSON.
- Require exact nonblank owner_issuer and owner_subject in the signed
  AssistX token, equal to the authenticated gateway principal.
- Require an equal owner tuple from AssistX authenticated live claim
  status immediately before provider dispatch, in addition to existing
  task/claim/agent/generation and lease checks.
- Reject missing, swapped or malformed owner identities and unowned
  internal-service bypasses in this strict mode.
- No inference, runtime admission, model load, router route mutation,
  database change or service deployment is performed.

The current deployed AssistX issuer and gateway do not provide these
new verified principal inputs. Enabling this flag now would intentionally
DENY otherwise-authorized requests. It must remain OFF until source
authority, authenticated gateway and replay custody are independently
demonstrated.

## Smallest executable cross-repo handoff

1. Establish immutable owner issuer/subject at authenticated Task
   creation using a verified source, not mutable task metadata.
   Scope idempotency keys by owner, rejecting owner switches.
   Legacy ownerless tasks remain non-admissible in strict owner mode.
2. Add the canonical stored owner to the AssistX Ed25519-signed
   executor token and authenticated claim-status response. Verify
   immutability and revocation from authoritative database state.
3. Add reviewed gateway authentication middleware setting an internal
   verified principal on the ASGI scope. Reject forged HTTP headers.
4. Run two separate authenticated principals through actual gateway,
   AssistX claim status and Auto-Router; negative-test swapped owner,
   wrong model, stale projection, revoked claim, cross-user retries,
   restart/replay and output receipt attribution.
5. Only then qualify Lenovo CPU for explicitly constrained degraded
   tasks; it is NOT equivalent to GLM 321B.

## Evidence

32 focused owner, JWT middleware and claim-fence tests passed.
Full Auto-Router nonintegration suite: 418 passed, 9 skipped,
1 live provider-registry drift failure previously reproduced in an
unmodified checkout (x1 model toolcall-v5-3b-combined-r2 not served).
Do not weaken the existing live drift test to force CI green.

This is a review-only consumer fence, NOT two-human-user acceptance
and not a complete owner identity producer. GLM production HOLD.
