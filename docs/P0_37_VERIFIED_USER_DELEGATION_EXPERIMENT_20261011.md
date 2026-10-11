# P0 #37 — opt-in signed user-delegation admission slice (2026-10-11)

**Status: experimental / DISABLED BY DEFAULT / DRAFT-ONLY / NOT APPROVED FOR DEPLOYMENT**

Stacked on the draft [two-user shadow receipts #45](https://github.com/scottjoyner/auto-router/pull/45), which itself depends on draft #42 → #36 → #35. This slice integrates a genuine **request-scoped signature-verification boundary** into the existing strict-live ASGI stack. It does **not** integrate a real identity provider, provider model/process witness, end-client delivery, or approved encrypted event custody.

## Code and exact behavior

- `src/auto_router/user_delegation.py` adds an Ed25519 verifier and an ASGI middleware, registered by `secure_live` **only when** the experimental feature flag is explicitly enabled. The verifier refuses to install unless the existing executor-inference auth is required, the internal gateway service bearer is configured, and an absolute PEM public-key file with a pinned key ID is provided.
- `secure_live` middleware ordering (outer to inner): existing executor-authentication → **optional gateway-signed delegation verifier** → existing request idempotency → existing route. The existing admission layer therefore rejects an unauthenticated caller before any delegated identity can be used.
- A gateway-signed, <=120-second Ed25519 JWT must contain an exact `typ`, `alg`, pinned `kid`, `iss`, `aud`, validated bounded tenant/subject/model labels, separate version-4 UUID root and attempt IDs, a unique jti, and valid `iat`/`nbf`/`exp`. The verified model handle must equal the request's model; caller-supplied `user`, metadata, tracing headers and shared bearer labels never determine the subject. Duplicate JSON fields, invalid Base64URL and non-canonical signature aliases are rejected.
- If the header is valid, its limited scalar fields live in the trusted **in-process ASGI scope**, not a client-controlled request-body metadata field. The idempotency layer derives a SHA-256 namespace from the signed issuer/tenant/subject/root/attempt and optional explicit idempotency key. Identical signed attempts are idempotent; a distinct signed attempt under one owner/root has its own record, and two actors sharing a service token can no longer collide in that delegated namespace.
- If **no delegation header** is present, requests keep legacy service/executor idempotency semantics. These callers remain **UNKNOWN HUMAN** for per-user trace purposes. If a delegation header is supplied but invalid, verification rejects it before reservation and routing. A signed delegation is accepted only with the *existing* authenticated internal service bearer.
- If the experimental feature was installed, a later attempt to turn off executor auth fails closed (503). The delegated body is read with a 256 KiB bound before parsing. No live provider calls, model loading, route selection or trace events are added by this slice.

**Important terminology:** despite the historical word `SHADOW` in the flag and class, enabling this experiment **is not purely observational**. Valid tokens change idempotency key partitioning; invalid tokens are rejected. It must NOT be enabled on a production ingress without separate review and controlled rollout. Nothing in these commits has been enabled on the running x1-370 router.

## Explicit deployment configuration (documentation only — DO NOT APPLY)

The following names define the experimental interface. No secret values or user tokens are recorded in this repository:

- `AUTO_ROUTER_P0_USER_DELEGATION_SHADOW_ENABLED` — absent/false by default.
- `AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED` — must remain true.
- `AUTO_ROUTER_INTERNAL_SERVICE_TOKEN` — existing private gateway-service bearer; its possession alone is **not** a human identity.
- `AUTO_ROUTER_P0_USER_DELEGATION_VERIFY_KEY_FILE` — pinned absolute path to an **independent, trusted gateway public Ed25519 PEM**. Not the AssistX executor key.
- `AUTO_ROUTER_P0_USER_DELEGATION_KEY_ID` — pinned signing key identifier; defaults are deliberately not accepted.

No production signer private key exists in this branch, no signing endpoint was built, and no actual gateway issued a human delegation. The JWTs in tests are minted by disposable keys and are **not** real-user proof.

## Tested synthetic identities against actual router middleware

The new `tests/test_p0_37_verified_user_delegation.py` constructs an isolated FastAPI instance with the **actual** executor-authentication, experimental delegation and request-idempotency middleware in the strict-live order, using disposable keys, an internal test service bearer and `tmp_path` SQLite.

Verified negative and positive behaviors include:

- Alice and Bob sharing the same existing service bearer and explicit idempotency string are forwarded as independent **signed** actors; an identical signed Alice request is rejected as a duplicate.
- Two signed attempts on one gateway-minted root remain distinct.
- Missing delegation preserves legacy shared-service behavior and **does not** invent an actor.
- Wrong signer, key ID, audience, model, actor syntax, attempt UUID, expired token, malformed envelope, duplicate header and wrong service bearer are rejected before an idempotency row.
- Non-canonical Base64URL signature encodings and >256 KiB delegated request bodies are rejected before routing.
- Startup remains disabled without explicit flag; missing pinned key fails closed; disabling existing executor auth with the experiment installed fails closed.
- Verified request scope never includes `provider_execution_attested`, client delivery status or physical process observations.

A broad isolated x1-370 regression at exact code SHA
`a7689a6acae2d8f9caf44c8d6b691a5b93490ed3` passed **493 tests,
11 skipped, 1 deselected** in 9.72 seconds. Ruff passed on the edited files.
The sole deselection is the **independently failing live provider-registry
drift** test. Unfiltered CI is therefore **NOT GREEN**. No matching hosted
CI run was established from this local test.

## Security and functional release blockers — do not overclaim

| Gate | Status | Required evidence before approval |
| --- | --- | --- |
| Separate signing/verifying keys for gateway delegations | **TESTED IN ISOLATION** | Operational key custody, rotation/revocation, permitted issuer, and gateway deployment |
| Real IdP / two authenticated humans | **OPEN** | Two independently authenticated real human principals at trusted ingress; anti-impersonation proof |
| Durable root ownership across restart and keys | **OPEN** | Persistent owner/root reservation proving one UUID root cannot be signed for two different subjects; no current production root-owner index |
| Replay and revocation beyond token expiry | **PARTIAL** | Approved durable per-actor attempt ledger and revoke semantics, including rotated signers and offline replay |
| Signed gateway caller-to-router channel | **PARTIAL** | Restrict forwarded credentials to the authorized gateway; independently prove no direct provider/user bypass can assert a human |
| Physical provider-process execution | **OPEN** | Correlate real provider process and model event with the verified request attempt (never infer from route config or SSE alone) |
| Trace custody / encrypted actor reads | **OPEN** | Approved sink, tenant + subject + producer + node authorization and offline spool encryption/reconciliation |
| Device response delivery | **OPEN** | Client- or transport-confirmed receipt, with UNKNOWN when unobserved |
| Safe model canary | **BLOCKED** | Beelink root ~3 GiB available and >20% I/O-full PSI; independent capacity/owner approval required |
| CI and production rollout | **OPEN** | Hosted exact-head CI, security review, gateway-first identity proof, staged canary with rollback |

**Release rule:** even after verified signed delegation, the router may know only that a trusted signing key asserted tenant/subject/model/root/attempt. It may **not** claim that a real IdP authenticated a specific person, that a model executed, that a response reached an iPhone, or that a tenant/actor trace is safely held until those independent sources actually exist.

No production routing, storage, model configuration, signing key or privacy setting was modified by this experiment.
