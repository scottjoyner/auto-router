# Supervised Auto-Router admin cutover: exact gates and rollback

**Status: HOLD / review only, October 11, 2026.** This file creates no deployment authority.
The running x1-370 router was observed mapping port **8088 on all IPv4 and IPv6 host interfaces**.
Anonymous `GET /admin/providers` returned **200** before the fix. Internet reachability
and actual sensitive value disclosure have **not** been established.

## What is already testable

- Source: Auto-Router draft #41 (inherited admin handlers require admin token; sanitized
  `GET /admin/providers/effective`, no live model probe).
- Client: AssistX draft #288 (authenticated provider catalog; only **404** falls back
  to authenticated old `/admin/providers`; no 401/403/5xx downgrade).
- Legacy read-only script callers: AssistX draft #290; missing token means NO admin
  request, not anonymous fallback.
- Repairs: AssistX drafts #289 and #291 (source binding, dashboard, output integrity,
  read-only receipt and gateway preflight repairs; do not merge the research bundle).
- Latest separate hosted three-way client composition: AssistX run **38148427107**,
  targeted **225 PASS**, full unit **833 PASS / 1 FAIL**, recovery **12 PASS**.
  The sole unit failure is tracked historical environment artifacts; **security issue
  AssistX #156 remains P0 NO-GO and is not waived**.

## Before changing network, tokens, deployment, or services

1. Operator validates legitimate direct LAN/Tailnet/iPhone/automation use of 8088.
   Internal `git_default` includes `git-router-1` and `assistx-api`; this is
   **not** proof that rebinding 8088 to localhost is safe for external clients.
2. Record current container image digest, exact deployment source SHA, start time,
   admin-client inventory, approved rollback image digest and rollback owner
   **privately**, without capturing environment values into public artifacts.
3. Account owners assess/rotate affected tracked credential artifacts as needed under
   AssistX #156; do not publicly commit a removed `.env` backup diff, silently
   rewrite repository history, or claim credentials were rotated.
4. Review and integrate independently owned fixes against actual main without waiving
   unchanged unit/recovery checks. Complete legitimate operator-auth acceptance.
5. Deploy verified AssistX client/legacy script authentication **before** router auth
   hardening. Verify old-router `/admin/providers/effective` HTTP 404 falls back only
   with the configured admin header; Fleet AVAILABLE != LOADED or admitted.
6. Approve a supervised router #41 image cutover and a reversible ingress policy.
   **Do not change Docker publishing or firewall rules from this documentation.**

## Read-only acceptance (run on the router host, after supervised cutover)

With no token required, probe anonymous behavior only:

```sh
python3 scripts/verify_admin_cutover_readonly.py --anonymous-only
```

Required: exit **0**, `anonymous_admin_denied=true`. Before the fix it is
expected to **fail**, which is an honest security signal.

For authorized verification, inject the existing approved administrator credential
into `AUTO_ROUTER_ADMIN_TOKEN` through the operator's secure environment, not in a
command argument, shell history, URL, Git issue or screenshot:

```sh
python3 scripts/verify_admin_cutover_readonly.py
```

Required: exit **0**, `anonymous_admin_denied=true`, and
`authenticated_snapshot=PASS`. Only loopback HTTP port 8088 is accepted.
No admin POST, provider reload, admission change, model request or route mutation
is performed. Script never prints token, response body, provider IDs or URL queries.

Supplement with separate authenticated operator UI and legitimate caller checks.
A passing read-only script is **not** proof of remote network isolation, model
residency, physical Destroyer/iPhone acceptance, request ownership or rollback.

## Fail / rollback

Stop promotion on any 200 anonymous admin response, auth downgrade, unrecognized
snapshot, owner/operator lockout, client 401/403 fallback, unavailable health,
unreviewed network restriction, or missing approved rollback source.

The supervisor restores **only the previously recorded and approved container
image/network configuration**, checks previously healthy endpoints, and records
sanitized time/status/commit witnesses. Do not infer authorization to restore
insecure WAN/LAN exposure; security owner must review the containment tradeoff.

No production container, network, credential, NAS data, model routing or device is
changed by this runbook.


## Independent remote negative acceptance (Destroyer AND Lenovo)

The localhost script is not a network-boundary or remote-client proof.
For each actual remote fleet node **after** the supervised cutover, copy or
use the reviewed `scripts/verify_remote_admin_auth_readonly.py` and run it
locally on that node, using the operator-confirmed x1-370 Tailnet IPv4:

```sh
python3 scripts/verify_remote_admin_auth_readonly.py --target-ip "$X1_TAILNET_IPV4"
```

This script accepts only an explicit literal private/Tailnet IPv4 address,
connects directly without an HTTP proxy, refuses redirects, performs **only
four anonymous GETs** (`/admin/providers`, `/admin/context`,
`/admin/circuits`, `/admin/agent-workers`), reads no response body, and
prints only anonymous HTTP status codes and an aggregate decision. It never
sends or accepts a credential. Source/tests are in draft PR #41.

Require both distinct physical-node receipts to report
`decision=PASS_REMOTE_401` and exit 0, with exact candidate source SHA
and capture time recorded without endpoint/body/token values. A 200,
403, 404, timeout, redirect or proxy path is **HOLD**, not a pass.
Treat a pre-cutover failure as expected evidence of the old router exposure.

This remote denial check is **independent** of authenticated internal AssistX
client acceptance. Do not interpret a 401 result as proof of provider
residency, model admission, legitimate caller functionality, WAN isolation,
credential custody, or the ability to roll back safely. No production
ingress or container configuration is changed by this runbook.

## Refreshed downstream compatibility evidence (Oct 11, 2026)

The earlier 833-pass/1-failure reference above is **historical**, not the
latest downstream state. At AssistX PR #288 head
`af96c45c6c5d4f3ebad969c7286e2c84e32f58c5`:

- [Pinned three-way acceptance 38152607289](https://github.com/scottjoyner/auto-assist/actions/runs/38152607289) **PASS** for targeted, full non-integration unit and recovery canary. Its dependencies were PR #291 `41c37863dd1b0b03585f2c77221796fb6795bb18` and PR #290 `5c2ace82bf92d80c2871b5f3d5b81688522d6aed`.
- [Source-binding composition 38152607284](https://github.com/scottjoyner/auto-assist/actions/runs/38152607284) **PASS**; [repair-stack composition 38152607282](https://github.com/scottjoyner/auto-assist/actions/runs/38152607282) **PASS**.
- Standalone [PR #288 CI 38152607337](https://github.com/scottjoyner/auto-assist/actions/runs/38152607337) remains **FAILED** and is not waived.
- A [read-only old-router compatibility receipt](https://github.com/scottjoyner/auto-assist/blob/af96c45c6c5d4f3ebad969c7286e2c84e32f58c5/docs/evidence/router-catalog-old-router-compatibility-readonly-20261011.json) records effective GET **404**, followed by configured-header legacy GET **200**, without deployment. It establishes compatibility, **not enforced authentication**: anonymous inherited admin GETs were also observed returning 200.

These are downstream **client** checks, not tests of this router PR #41 at a
physical deployment. The old router exposure and AssistX P0 #156 credential
custody hold remain. Do not deploy, change port publishing, or claim a remote
401 until both Destroyer and Lenovo independently return a signed-off
status-only `PASS_REMOTE_401` on the actual candidate build after supervised
cutover. A timestamp and exact candidate SHA must accompany the operator's
receipts; successful synthetic tests cannot substitute for them.

## Expanded localhost negative gate (draft, October 11)

The independent three-vantage [pre-cutover receipt](https://github.com/scottjoyner/auto-router/blob/security/effective-provider-snapshot-20261010/docs/evidence/router-admin-cutover-three-vantage-20261011T1551Z.json)
shows anonymous **HTTP 200** on `/admin/providers`, `/admin/circuits`,
and `/admin/agent-workers` from x1, Destroyer and Lenovo. Because the prior
localhost cutover script did not include the latter two routes, it could
have falsely passed while both remained exposed.

The updated `verify_admin_cutover_readonly.py` now requires anonymous
**HTTP 401** on all six local checks: providers, effective providers,
provider health, context, circuits and agent-workers. Offline regressions
individually simulate HTTP 200 on each endpoint and require HOLD without
sending credentials or reading bodies. The independent remote six-route
Destroyer/Lenovo check remains mandatory; local success is never a substitute
for either remote receipt.

**At the time of this note, the expanded test changes still require their
own completed exact-head CI results.** The known unpatched router remains
exposed to its Tailnet peers. Do not execute a router cutover from this
revised acceptance script or waive P0 AssistX credential custody #156.

## Six-route remote denial parity

Draft remote verification now requires anonymous HTTP 401 on the **same six
admin endpoints** as the localhost gate. The updated remote checker still
uses a literal private/Tailnet IPv4, no proxy, no redirects, no credentials,
no response-body reads and GET requests only. Independent offline tests reject
200, 302, 403, 404 and 503 on every individual route. Earlier four-route
remote receipts are insufficient for this new gate; physical postcutover
receipts must come separately from both Destroyer and Lenovo on the exact
reviewed candidate head. No production change is authorized by this update.


## Immutable candidate image identity gate (additional HOLD)

The actual x1-370 fleet Compose manifest is
`/media/scott/SSD_4TB/hermes-home/home_scott_.config/assistx-fleet/compose.auto-router-fleet.yml`,
service `llm-router`, container `git-router-1`.
Its current service image is a **floating `git-router` tag**, not an immutable
revision or registry digest. Its application code is packaged **inside the
image**, not bound from the reviewed checkout. Running GitHub CI against
source therefore **does not** establish that the fleet Compose command
will deploy the reviewed code.

The read-only `scripts/verify_release_image_identity_readonly.py` checks
the Compose service *structure*, candidate image ID, **OCI
`org.opencontainers.image.revision` label**, current running image ID and
an owner-approved locally available rollback image ID. It inspects only
narrow Docker metadata fields; it never builds, pulls, restarts, exposes
environment values, or executes route changes.

Before approving a supervised cutover:

1. Build a **distinct** candidate image from the exact reviewed PR #41 source
   (in a controlled build, outside the production router). Add a build
   provenance label matching the exact reviewed 40-character Git commit,
   preserve a signed or otherwise independently reviewed build receipt, and
   verify the image ID. A label by itself is metadata and is not proof of
   supply-chain integrity.
2. Create a **reviewed Compose override/release manifest** that refers to the
   intended candidate through an immutable digest or full image ID. Never
   rely on a floating `git-router:latest`/equivalent tag at the cutover.
3. Confirm the currently running router's image ID matches the owner-private,
   recorded **rollback** identity and that both images remain locally
   available. Keep network ports/mounts unchanged unless separately approved.
4. Run the read-only preflight using the operator-controlled manifest,
   candidate image ID, rollback image ID and exact reviewed source SHA:
   `python3 scripts/verify_release_image_identity_readonly.py --compose PATH --service llm-router --container git-router-1 --candidate-image-id sha256:... --rollback-image-id sha256:... --reviewed-source-sha FULL_40_HEX`.
   These IDs are image identities, **not credentials**. Do not paste any
   environment contents into arguments or CI comments.
5. Require `PASS_IDENTITY_PREFLIGHT_ONLY` and an independent operator's
   confirmation of the immutable build provenance and rollback procedure.
   That result **never** authorizes production release by itself; the
   previously described admin authentication, remote Destroyer/Lenovo,
   health, caller and credential-custody gates still apply.

**Observed pre-cutover state:** The actual current fleet manifest returned
`HOLD_IMAGE_IDENTITY` because it uses the floating tag. The script was
intentionally given the running image as both candidate and rollback, so it
also correctly flagged `CANDIDATE_EQUALS_ROLLBACK` and the absence of a
matching candidate revision label. No image was rebuilt or deployed.

This section remains an *operator-controlled release procedure*; it is not
a request to mutate container tags, images, networks or active services.
