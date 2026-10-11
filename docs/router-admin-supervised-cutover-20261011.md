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


### Docker storage-pressure gate (observed, do not build yet)

Read-only x1-370 check on October 11 found Docker's data root under
`/media/scott/SSD_4TB/docker`, on a **3.6 TB filesystem 97% used**
(about **109 GB available**), compared with the system root filesystem at
76% use. The currently present router image is roughly 292 MB. A new Docker
build can still create transient layers, caches and multiple images; the image
size alone is not a reliable upper bound on required staging capacity.

**Do not build or pull the candidate onto that Docker data root as an
automatic response to this runbook.** Before controlled image staging, the
storage owner must verify current headroom, agree on a capacity floor and
artifact retention policy, select an approved lower-pressure builder/cache
or free space through a separately verified non-destructive workflow.
Preserve the owner-private rollback image identity and keep its exact image
bytes available. Do not use unverified NAS mounts or delete images/cache
as an implicit repair. Capacity acceptance is separate from security #156,
source provenance, Compose digest pinning and the remote admin-401 gates.

This section remains an *operator-controlled release procedure*; it is not
a request to mutate container tags, images, networks or active services.

## Compose-to-candidate digest resolution (draft preflight hardening)

A digest-pinned Compose service and a separately inspected reviewed candidate
image are **not enough** to establish the image that would actually be
selected at cutover. The read-only image-identity preflight must also resolve
that exact Compose `services.llm-router.image` digest reference against the
local Docker image store and require its resulting immutable Docker image ID
to equal the operator-specified candidate image ID. Unavailable references,
wrong images, floating tags, source-label mismatches and a missing rollback
image all mean HOLD; the checker does **not** pull, build, deploy or restart.

This is a *local image selection* consistency check, **not** a signature,
reproducible-build or provenance attestation. An `org.opencontainers.image.revision`
label can be authored by a builder, and a correct image ID says nothing about
credential custody, whether the running router has been patched, or whether
the selected Compose configuration will be used by an authorized deployer.
The resulting record deliberately reports only reason codes, not image IDs,
repository digests, credentials, or unreviewed environment values.

New negative tests explicitly reject a syntactically valid Compose digest that
resolves to a different image. Run the exact-head hosted security contract and
full CI before treating this preflight as tested; do not infer deployment
approval from its PASS result or alter production to make it pass.

## Real Compose merge versus isolated override (draft acceptance correction)

The repository's checked-in `docker-compose.yml` defines
`services.llm-router.build: .`. Merely adding `image: sha256:...` in a
second file **does not remove that inherited build**; a command that supplies
both files can still build an image instead of consuming the reviewed archive.
A successful `docker compose -f override.yml config -q` **alone** does not
validate the production-shaped merge.

The undeployed CI candidate now writes `build: !reset null`, pins the exact
candidate image ID, and sets `pull_policy: never`. On an **isolated hosted
runner**, before loading the candidate, the restore job resolves the actual
checked-in base plus the artifact override with synthetic interpolation values.
It checks the resulting `llm-router` service has **no `build` field**, that
`image` equals the exact archive metadata's image ID, and that automatic
pulls are prohibited. The full merged Compose JSON, including potentially
sensitive environment fields, is **never printed or uploaded**. A failure is
a sanitized HOLD. Neither `config` nor this CI check starts a container.

This only validates the checked-in base and generated override from the same
Git source SHA. The real x1-370 operator may have other Compose overrides,
container names, environment files, external networks, or volume bindings.
**Do not reuse a hosted-runner PASS as evidence for that full private deployment
configuration.** The operator must privately verify the exact file order and
resolved router service configuration, stage only the approved immutable image,
retain the actual running rollback image, and independently satisfy the custody,
network, authenticated caller, and Destroyer/Lenovo remote-401 release gates.
No Docker build, image pull, or automatic `up` is authorized by this document.

## Production-specific Compose acceptance (Oct 11, metadata-only)

Read-only Fleet Commander inspection of the running x1-370 `git-router-1`
container identified **one actual operator Compose file** named
`compose.auto-router-fleet.yml`. This is **not** the checked-in
`docker-compose.yml` exercised by the disposable image-candidate workflow.
The running service is `llm-router`, running under Compose, and binds
container port 8088 to all IPv4 and IPv6 host interfaces. No public-WAN
reachability conclusion follows from these bindings.

The existing operator Compose structure (values and paths not published) has
an image reference **by mutable name/tag**, no `build` entry, and no explicit
`pull_policy`. Its environment and mount sources are private and were
**not** copied into this repository. A read-only `docker compose config
--no-env-resolution --format json` inspection against the actual file
returned a deliberate `HOLD_COMPOSE_STRUCTURE` for
`IMAGE_NOT_PINNED_TO_RUNNING_IMMUTABLE_ID` and
`PULL_POLICY_NOT_NEVER`. No Compose config JSON, environment variable
values, private paths, image identifiers, or secrets were printed.

**New review-only gate:** `scripts/verify_private_compose_candidate_readonly.py`
takes the *actual operator Compose file stack in deployment order*, and an
approved exact `sha256:` candidate image ID. It runs only
`docker compose ... config --no-env-resolution --format json` (no `up`,
`pull`, `build`, network probe or restart), captures output privately,
and prints reason codes only. A PASS requires the resolved `llm-router`
service's `image` to equal the candidate ID, its `build` to be absent
and `pull_policy` to equal `never`. A missing file, interpolation
failure, malformed response or mismatch returns HOLD. Unit tests exercise
synthetic secrets without logging their values.

The operator should run the verifier **only on an approved, locally
reviewed candidate file stack** after independently capturing the currently
running image/rollback metadata. Do **not** modify the live Compose file to
force a PASS, substitute the public CI base for the actual private base,
or print `docker compose config` in a shared log. The gate does not
evaluate private network/mount authorization, credential custody,
service continuity, inference admission or rollback capability. Each
remains an independent release condition.

Fresh Docker evidence: x1-370's current router container is running and
the existing image is locally present. Its Docker data filesystem is about
**97.1% used**, with about **107 GiB** free. This is a staging HOLD
pending storage-owner acceptance; no images or caches were deleted or
changed. P0 AssistX credential custody #156 remains unresolved. The
router is **not** on PR #41, and physical postcutover six-route 401 checks
from Destroyer and Lenovo have not been performed.

**Decision: DRAFT / NO DEPLOY.**

## Executable synthetic private-Compose parity gate — PASS, not release authority

The operator-safe read-only tool now provides two complementary modes:

- `--synthetic-overlay` checks that the *actual* private ordered Compose
  file stack can accept an in-memory fake immutable image and
  `pull_policy: never`, without modifying any other resolved router field,
  other service, network, or volume. The tool uses `docker compose config`
  only, with neither file writes nor image/container actions.
- `--expected-image-id sha256:...` checks the *actual approved* candidate
  image identifier, absence of inherited `build`, and `pull_policy: never`
  after the operator has prepared the approved Compose override. It does
  not certify the immutable image is staged, signed or safe to deploy.

The operator should supply each `--compose-file` in the exact deployment
order, keeping full private paths, environment values and raw Compose
output in owner custody. The tool deliberately emits only status codes,
boolean checks and reason codes. Do **not** attach `docker compose config`
output to GitHub, Signal or other shared logs.

The exact committed verifier source `4465a6935bfc7cea5a7a8d1d846ddb7a0ba222cf`
was streamed into a one-shot Python process on x1-370 and executed
**without writing any local file** against the live container's actual
Compose-file metadata. It returned
`PASS_SYNTHETIC_OVERLAY_STRUCTURE_ONLY` with all eight checks true:
synthetic image selected; automatic pull prohibited; no build; router
other fields unchanged; other services unchanged; network config
unchanged; volume config unchanged; and the **entire resolved Compose
document unchanged** apart from the permitted image and pull-policy
fields (including top-level project, secrets and configs). This check compared full resolved
objects privately, without printing source values. See
[`docs/evidence/router-private-compose-synthetic-readonly-20261011.json`](evidence/router-private-compose-synthetic-readonly-20261011.json).

The baseline unmodified private configuration **still HOLDs**: the image
reference remains a mutable tag and pull policy is not explicitly
`never`. No candidate image is staged on x1, the original image
remains running, and the 97%-used Docker root retains an independent
storage-pressure HOLD. The synthetic test proves a *configuration
transformation is structurally possible*, not an approved actual
deployment plan or production admission.

**Separate blockers still required:** AssistX security custody #156,
explicit owner-approved rollback image and storage plan, approved
candidate source/image provenance, authenticated client-first acceptance,
and *postcutover* independent six-route anonymous-401 receipts from
x1-370, Destroyer and Lenovo. All production changes remain forbidden
by this runbook without an operator-approved cutover.

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

