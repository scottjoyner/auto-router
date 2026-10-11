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

## Safer off-host disposable canary opportunity (not staged)

Before any local image action, I inspected read-only Docker availability and
storage capacity through strict-host-key SSH from x1-370:

| Fleet node | Docker daemon | Docker root used | Free |
|---|---|---:|---:|
| **xwing** | Reachable | **58.7%** | **655.2 GiB** |
| Destroyer | Reachable | 84.2% | 74.0 GiB |
| Lenovo | Docker unavailable | — | — |
| x1-370 (current router) | Reachable | **97.08%** | **107.04 GiB** |

[Sanitized per-node capacity witness](evidence/pr55-offhost-disposable-canary-capacity-20261011.json).

**Recommendation:** If the owner explicitly approves a *nonproduction*
physical canary, prefer **xwing** over writing additional Docker layers
to x1's pressured root. Use only the independently verified reviewed
immutable image and an isolated, short-lived container: no network,
no published ports, read-only root, no new privileges, dropped Linux
capabilities, bounded CPU/memory/PIDs, synthetic authentication values,
no mounted production paths and automatic cleanup. Verify the restored
image's revision/digest and all admin-denial routes independently.
This is a **candidate** selection from storage metadata only; xwing's
current inference load, reservations, available RAM and safety of
starting even an isolated test are **not yet approved or verified**.

No image was copied, staged, loaded or removed on xwing, Destroyer,
Lenovo or x1 as part of this examination. A PASS from xwing would
validate *off-host packaged runtime behavior*, not production x1
Compose continuity, firewall security, physical deployment or
credential custody. Production cutover remains under owner control.

## Known legacy client compatibility gaps

- **AssistX coordination status** already uses an authenticated,
  no-redirect context probe in
  [admin script PR #290](https://github.com/scottjoyner/auto-assist/pull/290).
  The existing docs-sync verifier in that **same branch** also calls the
  safe probe; do not infer that the corresponding unmerged `main` scripts
  are migrated. This client-side PR remains separate and must be deployed
  **before** a router-side auth requirement reaches those callers.
- The emergency branch now includes the independently review-tested
  browser shortcut hygiene from PR #41. `src/auto_router/ui_pages.py` and
  the four operator HTML templates render protected `/admin/` endpoints
  as **labeled non-clickable references**, rather than ordinary anchors
  misleadingly offering access without the `X-Admin-Token` header. Public
  pages remain clickable. `tests/test_admin_ui_link_safety.py` forbids
  hardcoded admin anchors, validates template rendering, and prohibits
  embedding admin tokens into links, JavaScript or query strings. This
  change grants **no** new authentication authority or browser access:
  intentional 401 responses still apply to direct uncredentialed admin GETs.
  A separately reviewed server-side operator-auth UI remains future work.
## Browser operator action compatibility (security-preserving)

A static review found **seven** HTMX `hx-post` controls in the operator
dashboard and ops summary that would try to call protected admin actions
without the `X-Admin-Token` header. Those include provider probing,
service scanning, agent-CLI discovery and outbox dry-run dispatch. They
were distinct from the ordinary `/admin/` browser navigation links
already marked as not directly accessible.

The draft templates now show those seven actions as **nonclickable,
labeled API references**, preserving their descriptive context without
initiating an unauthorized request or putting any shared admin token
in browser JavaScript, a header set by untrusted JS, URL query strings,
HTMX, or unreviewed telemetry. A repository-wide HTML regression test
rejects literal privileged `hx-get`/`hx-post` and other HTMX admin
actions and retains the seven known controls as inert references.

**Operator tradeoff:** browser-triggered provider discovery, CLI scan,
service scan, and outbox dry-run buttons are unavailable after this change.
The router's separately authenticated API clients can still perform
approved operations. A replacement interactive operator UI would require
a distinct security-reviewed server-side session/authentication design.
This UI change is not a router authorization bypass, does not resolve
shared-token transport or credential custody, and does not authorize
deploying the candidate.

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

### Public dashboard configuration URL redaction

The existing public dashboard and ops-summary fragments previously displayed
raw configured **AssistX task/event-sink URLs**, plus gateway base/OpenAI
URLs. Even without URL userinfo or query parameters today, future paths
could contain credentials and the raw private origin exposes internal
topology. The draft presentation now shows only **configured / not
configured** and hides destination values. The back-end authorized
administrative APIs have **not** changed shape or been given new
permissions. Synthetic Jinja regression tests inject URL values with a
credential-looking path/query marker and fail if that marker or private
hostname appears in any of the six affected rendered expressions.
This is **public HTML output hygiene**, not transport encryption or an
authorization fix for the dashboard itself. Separately audit other public
routes before claiming complete information-disclosure coverage.

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
