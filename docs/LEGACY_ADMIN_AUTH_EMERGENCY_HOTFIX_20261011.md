# Minimum inherited-admin authorization hotfix — alternative to PR #41

**DRAFT ONLY / NOT DEPLOYED / NO PRODUCTION APPROVAL.** This is a
deliberately narrow alternative proposed under
[security issue #40](https://github.com/scottjoyner/auto-router/issues/40).
It is **mutually exclusive** with the overlapping inherited route edits in
[PR #41](https://github.com/scottjoyner/auto-router/pull/41). Do not merge
both branches mechanically or mistake either one's CI for physical acceptance.

## Why an emergency source-only alternative exists

Three real Tailnet vantage points (x1-370, Destroyer and Lenovo) independently
received anonymous HTTP 200 on five inherited admin GETs over private IPv4,
and Destroyer/Lenovo independently repeated the result over private IPv6.
The old router does not have the new sanitized providers/effective route,
which returned 404. The router also has legacy admin POST operations that
were unguarded in source review; no unauthorized POST was sent to production.

Full PR #41 includes a sanitized catalog and image/Compose rollout machinery.
This alternative changes *only* the existing twelve inherited admin handler
signatures in `src/auto_router/main.py` to require the already configured
central `Depends(require_admin)`. Existing auth rejects missing or incorrect
admin tokens and fails closed when none is configured. It preserves all
existing response formats for callers carrying the correct token; it does not
add, rename, enable or load models and does not add an endpoint.

## Source-level acceptance

- Focused lifespan-free synthetic tests verify every current legacy admin
  GET/POST route registers the direct dependency, rejects anonymous and
  wrong-token HTTP requests, fails closed when token config is absent, and
  cannot call circuit reset or model reconciliation when unauthorized.
- A separate hosted workflow runs that test against its exact checkout.
- The existing containerized CI smoke is extended to send missing-token
  admin GETs and missing/wrong-token admin POSTs **only** to the disposable
  CI router. It discards all response bodies.
- No physical POST, model probing, local service restart, firewall rule,
  token rotation, Docker image staging, or NAS operation is performed.
- The branch's image workflow supplies independent **undeployed** packaging,
  archive and restored-image evidence; this is not a signed production
  certificate. Actual private Compose, image custody, rollback and security
  owner approval remain mandatory before any physical deployment.

## Pinned, undeployed emergency image acceptance

The emergency hotfix now has a **separate** disposable GitHub Actions
[undeployed-image workflow](../.github/workflows/emergency-hotfix-pinned-image.yml).
It does not reuse a PR #41 image, tag or provenance receipt.

- On the reviewed exact PR head, CI resolves an upstream Python base
  **digest**, builds the same single-purpose Dockerfile with a
  `org.opencontainers.image.revision` label and refuses tracked environment
  variants in image inputs. The only Dockerfile changes are optional pinned
  base/revision arguments and build-time creation of the legacy installed
  package's `static/vendor` path so import succeeds on a read-only root.
- It imports the actual packaged `auto_router.secure_live:app` in a
  disposable **network-disabled/read-only/capabilities-dropped** container,
  refuses a missing direct `require_admin` dependency for any mounted admin
  route and tests anonymous/wrong-token rejection across **every registered
  admin GET or state-changing HTTP method**, including dynamic paths.
  Authorized synthetic empty-state `GET /admin/providers` must still return
  the expected legacy response. The **new** `/admin/providers/effective`
  from full PR #41 must remain absent on this narrow hotfix.
- CI exports an image archive + archive SHA-256 + immutable Docker image ID,
  revision metadata and an **undeployed** Compose override, with a short
  retention period. A second disposable runner compares the archive digest,
  source revision and resolved repository **base + override** Compose model
  (`build` absent; candidate ID exact; `pull_policy: never`), reloads the
  archive and independently reruns the packaged admin-route denial checks.
- **A green hosted build is necessary, never sufficient.** The checked-in
  Compose base is **not** the actual x1-370 operator-private fleet Compose
  stack. The physical host's image ID, rollback image, source-bound candidate,
  Docker filesystem capacity and private override must still be accepted
  separately. No image archive is transferred to x1 by the workflow.

This minimal hotfix intentionally retains the legacy authorized
`/admin/providers` response schema, which serializes provider configuration.
A privileged caller may therefore receive configuration-sensitive fields.
Use the admin credential **only in trusted service/operator custody**, never
in a browser, untrusted trace, or publicly logged request. The sanitized
`/admin/providers/effective` route in PR #41 is a separate enhancement.

**Do not infer release readiness until all exact-head workflows finish
successfully; CI jobs on earlier commits are not evidence for later heads.**

## Actual x1-370 private Compose and rollback preflight

The exact reviewed emergency-handoff verifier in
[`scripts/verify_private_compose_candidate_readonly.py`](../scripts/verify_private_compose_candidate_readonly.py)
was reused from full PR #41 and executed from **PR #55 head
`c9a20cf6a545d5de70fcb5c4c505e5bad9c61408`** in a read-only one-shot
Fleet Commander process. It inspected the real operator
`compose.auto-router-fleet.yml` stack (one file), not the repository's
public Compose base. `--synthetic-overlay` passed **eight checks**, including
entire resolved Compose document parity apart from the synthetic image
reference and never-pull policy. It did not write an override file, load an
image, open a router HTTP connection, or print private Compose values.

The unchanged **live** Compose stack correctly returns
`HOLD_PRIVATE_COMPOSE`, with
`COMPOSE_IMAGE_NOT_EXACT_CANDIDATE_ID` and
`COMPOSE_PULL_POLICY_NOT_NEVER`. A synthetic match establishes only that
a valid *image selection* override is structurally possible. It does not
validate a particular production image, signatures, runtime peers, or
postcutover stability.

An independent physical read-only rollback/capacity checkpoint observed:

| Custody / storage property | Result |
|---|---|
| Existing `git-router-1` container | Running |
| Original running image retrievable by immutable ID | **Yes** |
| Original image virtual size | **~278.8 MiB** |
| Existing container restart policy | Present |
| PR #55 GitHub candidate compressed archive | **76,416,730 bytes** |
| x1 Docker root used / free | **~97.08% / 107.04 GiB** |
| Reviewed candidate staged on x1 | **No** |
| Owner-approved rollback execution or storage cleanup | **No** |

[Value-free x1 preflight and archive evidence](evidence/pr55-private-x1-release-preflight-20261011.json).

**Important:** the archive's compressed size is not the same as Docker's
unpacked layer requirements, nor is `docker image inspect` proof the
rollback container can restart with its current secrets, networks and
mounts. Preserve the existing image and container until the owner
independently proves rollback; do not `docker prune`, rebuild the old
image from a mutable tag, or change storage policy to clear this gate.

## Known legacy client compatibility gaps

- **AssistX coordination status** already uses an authenticated,
  no-redirect context probe in
  [admin script PR #290](https://github.com/scottjoyner/auto-assist/pull/290).
  The existing docs-sync verifier in that **same branch** also calls the
  safe probe; do not infer that the corresponding unmerged `main` scripts
  are migrated. This client-side PR remains separate and must be deployed
  **before** a router-side auth requirement reaches those callers.
- `src/auto_router/ui_pages.py` still contains direct browser links to
  legacy JSON admin routes (providers, context, circuits, agent workers).
  These links correctly return HTTP **401** once the hotfix is active unless
  the operator has a trusted server-side authenticated path. **Do not**
  insert the four-service shared admin token into browser JavaScript,
  query strings, URLs or public logs to preserve this legacy navigation.
  A security owner must explicitly accept the temporary 401 UX or
  approve a separately reviewed safe UI workflow.
- Authenticated legacy `GET /admin/providers` retains the original full
  provider serialization, potentially including configuration-sensitive
  fields. **Only trusted authenticated callers** should receive it.
  This hotfix does not provide PR #41's sanitized effective-catalog route.
- Known server-side scripts and browser anchors are **not an exhaustive
  caller inventory**. Authenticate each actual API, worker and Hermes
  configured-origin path in owner custody and verify any operational
  automation before approving the source-only change.

The actual emergency hotfix candidate now has the *source, synthetic
tests, hosted image and private Compose transformation* evidence. It
**does not** have signed owner release approval, staged physical candidate,
privileged Docker network/firewall access review, complete caller
acceptance, or successful postcutover physical authentication.

## Security-owner release choices

**Option A — urgent reversible interim ingress containment** may precede
code deployment, but requires actual dual-stack Docker firewall/forward/NAT
observation and per-consumer continuity. Unprivileged netfilter inspection
currently fails. Review
[the owner containment gate in PR #41](https://github.com/scottjoyner/auto-router/blob/security/effective-provider-snapshot-20261010/docs/ROUTER_ADMIN_EMERGENCY_CONTAINMENT_DECISION_20261011.md).
A network deny is not authenticated HTTP 401.

**Option B — a narrowly staged hotfix** can repair inherited handler auth
without the separate provider catalog migration, but it intentionally
breaks every legacy anonymous admin caller. Owner must inventory actual
API, worker, Hermes, browser/operator and tool clients, update authorized
client headers **first**, preserve existing token custody and rollback,
privately confirm real Compose image and port dependencies, and independently
verify x1/Destroyer/Lenovo postcutover six-route auth status. Note
`/admin/providers/effective` remains **404** on this minimal hotfix; the
full six-route **401** criterion from PR #41 therefore must be adapted to
an explicit hotfix-only route inventory (existing inherited routes 401,
new endpoint 404), without misrepresenting the missing endpoint as
deployed. Never mark full catalog acceptance complete from a hotfix.

**Option C — full PR #41 supervised client-first release** remains the
more complete integration candidate, but its independent security/storage
gates apply.

The separately open historical secret-history custody
[AssistX #156](https://github.com/scottjoyner/auto-assist/issues/156)
and four-service shared admin token transport/rotation
[AssistX #299](https://github.com/scottjoyner/auto-assist/issues/299)
do not disappear from any option. An owner may separately authorize
urgent proportionate mitigation of the known live bypass, without claiming
these broader remediation tasks are complete.

**Next gate: named security owner, exact candidate/image, per-consumer
authenticated continuity, dual-stack denial/rollback plan, and physical
validation. No merge or deployment was authorized by this document.**
