# Experimental admin-key overlap: decision boundary, not a rollout

**STATUS: DRAFT / UNWIRED / NOT DEPLOYED.** This is a *separate* branch from
Auto-Router PR #41. It must not be merged into the production auth entrypoint
or used as an excuse to rotate production credentials without a security
owner's explicit approval and an accepted private transport plan.

## Why investigate?

AssistX issue #299 verifies one same-byte admin credential across the
running router, API, worker and Hermes adapter. Router `security.require_admin`
currently accepts just `AUTO_ROUTER_ADMIN_TOKEN`. API, worker and Hermes can
all reach the already-protected status endpoint via their individual configured
private origins, but those origins are plaintext HTTP on private networks.
The on-host router still exposes five inherited admin GET routes anonymously.
Auto-Router PR #41 fixes those GETs *in code*, not on the running router.

With no dual-token overlap, rotating only the router first would strand all
three legitimate consumers until their credentials were changed. Coordinated
stop-the-world maintenance is the conservative default.

## Research artifact (pure function only)

`src/auto_router/research_admin_overlap.py` is **never imported** by
`security.py`, `settings.py`, `main.py`, `main_live.py` or
`secure_live.py`. The helper:

- Allows the current primary token independently of previous-key configuration.
- Rejects previous-key acceptance unless **both** explicit feature enable
  and owner-approved-window booleans are exactly true.
- Accepts at most a 15-minute start-to-expiry UTC interval.
- Requires a monotonic activation-time anchor; refuses backward monotonic
  movement, monotonic expiry and wall-clock rollback.
- Refuses blank, short, non-ASCII, control-character, or whitespace-margined
  research credentials and identical primary/previous keys.
- Uses constant-time comparison for accepted credentials and returns a
  three-state enum, never the bytes, hashes, or endpoint identities.

Research-only trust assumptions: caller must supply reliable wall/monotonic
timestamps and bind activation to one process; false clocks, process
restarts, crash/rollback and a compromised deployment host can invalidate
clock-based guarantees. A monotonic activation anchor alone is not durable
across process restarts. This proof does not address credential distribution,
token issuer revocation, credential theft, replay, or authenticated transport.

## Conditions before ANY integration proposal

1. Assign a named security owner, independent reviewer, issuer custodian, and
   rollback owner. Record exact four-service configuration custody without
   printing secret values, hashes, or screenshots.
2. Provide an independently verified router peer-identity/encrypted transport
   solution or explicit, time-limited owner-approved exception. Never
   reconfigure the router to accept two shared keys over unaudited HTTP merely
   because the pure helper tests pass.
3. Specify whether overlap is safer than a maintenance-window transition.
   Default to maintenance window if uncertain.
4. Design durable expiry across restarts and clock rollback (or ban restarts
   during the approved window with independent enforcement); test split
   clients, restart, expiration, clock attacks, rollback, revocation and
   log redaction. Do not silently use the pure function as the runtime gate.
5. Determine one-and-only-one credential presentation channel. Current auth
   permits header, bearer and Basic alternatives; a future integrator must
   explicitly reject conflicting concurrent channels and prevent credential
   redirects/proxies. This research helper deliberately accepts only *one*
   presented credential and does not enforce HTTP transport properties.
6. Review and test acceptance in a disposable strict-live ASGI container,
   while keeping every inherited admin handler protected and public admin
   responses sanitized. Run client-first integration and host+remote IPv4
   AND IPv6 ingress checks. Preserve independent admission and proof gates.
7. Do not conflate this shared-admin credential with the **14 separate**
   historical/live configuration matches under AssistX #156.

## Exact limits of evidence

Pure helper tests are not proof of zero-downtime transition, successful
rotation, revoked historical credentials, TLS peer identity, protected
production admin routes, model residency, or deployment authorization.
No production .env, Docker, Tailscale, firewall, inference, or Git history
was changed to prepare this proposal.

**Disposition: RESEARCH_ONLY.**


## Channel arbitration and four-service transition proof

The unwired helper now also models *raw* synthetic HTTP header pairs. It
rejects more than one authentication presentation channel even if one is
correct: duplicated `X-Admin-Token` (including casing variants), mixed
`X-Admin-Token` and Bearer/Basic, multiple `Authorization` headers,
unsupported schemes, folded/comma-joined Bearer, noncanonical or malformed
Basic base64, wrong Basic username, control characters, non-ASCII, underscore-aliased `X_Admin_Token`, invalid header-name grammar,
and oversize/invalid raw headers. Basic credentials are decoded only in memory;
the result is an enum, never a credential or parsed headers. This is NOT a
production header/middleware implementation. The trusted ASGI/proxy boundary,
TLS peer identity, and management of multiple auth sources remain unproven.

The separate `tests/test_research_admin_transition_model.py` exhaustively
checks all 24 sequential update orders for four abstract participants
(router, API, worker, Hermes), assuming constant admin-request demand. With
a single current token and no request drain, every serial transition has
some intermediate authentication failure. A deliberately drained
maintenance interval prevents requests during the mismatch but is **not**
zero downtime. A theoretical overlap requires the router to enable bounded
old+new acceptance first and every client to move before old-key expiry.
A missed client, prior client-first activation or expired window still fails.
These are discrete synthetic checks, NOT observed latency or availability
guarantees. The actual shared credential is never used in these tests.

**Decision recommendation:** Until owners can show restart-safe expiry,
conflicting-channel denial in the real ASGI pipeline, authenticated transport
and independently approved issuer/token custody, prefer a supervised
maintenance window over dual-token runtime acceptance. Importing this research
module from production authentication would require a new explicit security
review, separate branch, and negative end-to-end tests.
