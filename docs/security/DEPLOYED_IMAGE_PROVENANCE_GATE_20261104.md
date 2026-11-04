# Deployed Auto-Router image identity and administrative authentication gate

**November 4, 2026 · read-only release evidence · production remains HOLD**

New operator utility:
`scripts/verify_deployed_admin_release_identity.py`.

## Why it exists

A live inspection on x1-370 established a physical source
provenance mismatch. The running `git-router-1` container
restarted on November 4 but its immutable image ID was
`sha256:182d3d3c8aa6b8afbc45bb5094493f74baa58dbb5c0b0b54555b5718f03c1dd8`
from an image **built October 7, 2026**. The running
container's installed `main.py` and `security.py` source
SHA-256 hashes differed from the reviewed admin-auth candidate.

The 11:02 EST live anonymous, read-only HTTP probes returned
200 on `/admin/providers`, `/admin/context`,
`/admin/providers/health`, `/admin/circuits` and
`/admin/agent-jobs`. Only `/admin/admission` returned 401.
The production container published TCP 8088 on both
`0.0.0.0` and `::`. No response body was read, and no
credential or live mutation was attempted.

A container restart is **not** a fix for a stale image.

## Implemented proof

This verifier takes explicit, separately validated SHA-256
pins for the **approved built image** plus its four installed
Python source files `main.py`, `security.py`,
`main_live.py`, `secure_live.py`.

It reads only selected Docker inspection fields, verifies
the container remains running with
`auto_router.secure_live:app`, checks the expected host
port binding and probes six predeclared admin GET routes
without authentication. It never reads response bodies,
calls an admin POST, accesses Docker secrets or changes
containers. The image-and-start-time lease is rechecked
after HTTP probes, preventing a container restart from
being mistaken for a successful continuous inspection.

**Fail closed** unless every check passes:
- full, lower-case SHA-256 expected pins are supplied by
  an independent approved image build receipt
- running container has exactly the pinned immutable image
  and four exact installed Python-source hashes
- `secure_live:app` is the launched ASGI entrypoint
- TCP 8088 maps to approved loopback bindings only,
  unless an operator explicitly opts in to public host
  bindings by adding `--allow-public-bind`
- **all six** ordered expected admin requests return HTTP 401
- container image ID and start timestamp remain unchanged
  through the complete probe

A green result is called
`PASS_PRE_CUTOVER_INSPECTION_ONLY`, **never production-ready**.
It cannot prove approved operator credentials still work,
external network protection, source-key custody, provider
availability, fleet-client compatibility or safe rollback.

## Operator invocation after an approved build

The image digest **must come from the approved build**
(not read from live and reused as its expected value).
Exact reviewed source hashes should come from the pinned
builder's validated artifact, not the running container.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B \
 -m scripts.verify_deployed_admin_release_identity \
 --expected-image-sha256 "$APPROVED_BUILD_IMAGE_SHA256" \
 --expected-main-sha256 "$APPROVED_MAIN_SHA256" \
 --expected-security-sha256 "$APPROVED_SECURITY_SHA256" \
 --expected-main-live-sha256 "$APPROVED_MAIN_LIVE_SHA256" \
 --expected-secure-live-sha256 "$APPROVED_SECURE_LIVE_SHA256"
```

The default assumes loopback-only publishing. Operators
must explicitly decide whether public TCP 8088 is intended
and, if so, approve the exception separately.

## Actual negative-control result

With the existing old image ID supplied *only to isolate*
the source-mismatch check, and the reviewed candidate source
hashes supplied separately, the live probe **returned HOLD /
exit 2**, because source hashes did not match and five
anonymous administrative reads still returned 200.
This negative control was not a deployment rehearsal and
does not provide an approved image ID for an eventual rollout.

Synthetic tests include altered image, different source
hash, wrong ASGI entrypoint, public binding without opt-in,
mismatched port, six-path canary truncation/reordering,
anonymous-200 leak, and container instance restart.
All test-only simulation is performed without Docker.

**Pre-cutover acceptance still requires reviewed build
provenance, correct credentials, rollback evidence,
authorization and separate operator approval.**
No production rollout is performed here.

## Hosted CI build-to-checkout provenance addition (11:18 EST)

PR #129's Docker integration job previously checked six anonymous
admin HTTP 401 statuses on the newly built `auto-router-smoke`
container, but it did **not** confirm that the container's installed
`auto_router/*.py` files were the reviewed Git checkout's bytes.

The new `scripts/verify_ci_built_image_provenance.py` addresses
this additional trust gap without entering production. It runs
*after* the existing admin 401 smoke check and only against
the existing `auto-router-smoke` CI Docker container. It:
- Pins the ephemeral container's immutable Docker image ID and
  start timestamp before and after the complete proof.
- SHA-256 hashes the **installed** `main.py`, `security.py`,
  `main_live.py` and `secure_live.py` inside the built
  container, without importing application code or reading
  credentials, then compares all four with the Git checkout.
- Verifies those four source files are Git-tracked and unmodified
  relative to the checkout commit (and records that commit SHA).
- Rejects unexpected bind/volume mounts that could obscure
  the packaged source files; in this CI profile, only
  `/app/config` and `/data` are permitted.
- Verifies the container is still running the expected
  `auto_router.secure_live:app` entrypoint with loopback-only
  TCP 8088 publishing, and all six ordered anonymous
  administrative GET requests return HTTP 401.
- Emits a private, nonsecret `ci-artifacts/router-ci-built-image.json`
  proof, retained for 14 days by `actions/upload-artifact@v4`.
  A failed proof exits nonzero and blocks CI.

The artifact's `PASS_CI_BUILD_ONLY` status proves consistency
of the **temporary hosted CI build** with its tested Git
checkout. It is neither a signed, independently witnessed
provenance record nor an approved production image/promotion.
The deployed-image gate still requires a **separately
approved exact image ID**, source pins, credentials/rollback
canary, and human release authorization before cutover.

Local negative tests simulate mismatched installed bytes,
unexpected code mounts, wrong source checkout revision,
image/container restarts, anonymous admin 200 and unsafe
receipt paths. All 48 combined relevant security tests
passed on x1-370; strict import/name lint and workflow
YAML parsing passed. Hosted exact-head build proof remains
unverified until the revised PR workflow actually runs.

## Additional synthetic positive-operator gate (12:06 EST)

The Docker integration workflow now invokes
`python3 -B -m scripts.admin_positive_auth_ci_canary
--explicit-ci-synthetic-only` after anonymous-admin denial
and before source-to-image provenance verification. It is
restricted to `CI=true` and `GITHUB_ACTIONS=true`,
loopback-only TCP 8088, and the previously committed
**synthetic** `smoke-admin-token` fixture found in
`docker-compose.integration.yml`. No real operator token
is loaded, logged or used.

For each of five **safely read-only** admin routes
(`/admin/providers`, `/admin/context`,
`/admin/circuits`, `/admin/agent-jobs`,
`/admin/admission`), the script requires:
- anonymous HTTP 401
- invalid synthetic fixture credential HTTP 401
- correct synthetic fixture credential HTTP 200

All 15 requests are GET and status-only. No response body
is consumed or saved. The sixth administrative endpoint
`/admin/providers/health` remains covered by the existing
anonymous-only 401 canary, but is **excluded** from
positive-auth probes because its handler performs external
provider-health checks and can write local signal snapshots.
Likewise no admin POSTs or inference requests are sent.

A successful result is strictly `PASS_CI_FIXTURE_ONLY`,
**never evidence that a real production operator credential
works**. Deployed credential verification must remain an
explicitly authorized, separately reviewed human release gate.

Nineteen synthetic unit tests passed, including wrong-token
acceptance, 401-for-valid-token, missing route, unsafe
path/port/credential, unexpected environment, and accidental
body-read rejection. Source and production remain unchanged.

## Optional operator-controlled read-only credential acceptance (12:10 EST)

New `scripts/approved_operator_readonly_canary.py` prepares a future
**explicitly authorized** post-cutover verification step. It is not
invoked from GitHub CI and has **never** been run against the current
production router.

The operator must provide the independently approved build's full
Docker image SHA-256 and all four reviewed source SHA-256 values,
supply `--explicit-approved-read-only-operator`, and pass the real
operator credential through an already-open private **pipe file
descriptor** numbered 3–32. The script refuses file-backed secrets,
command-line secrets, standard input, malformed credentials, stale
image/build source identities, public bind mismatches (unless separately
reviewed through the lower-level provenance tool), incomplete six-path
anonymous 401 checks and container restarts.

**Only after** deployed-image identity and all six anonymous denials
are revalidated does it read the credential from the pipe. It then
makes exactly five sequential status-only GETs against loopback TCP
8088: providers, context, circuits, agent jobs and admission. All
five must return HTTP 200. It never reads response bodies, issues a
POST, performs inference, or requests provider-health probes. The
credential is never printed, stored in a receipt or accepted on argv.

A success result is `PASS_READ_ONLY_OPERATOR_PROBE_ONLY`,
**not production-ready**, and cannot establish independent rollback
approval, external ingress isolation, signature/key custody, or
authorization to restart/redeploy a live container. It is
deliberately separate from the synthetic `smoke-admin-token` used
in the CI Compose fixture.

**Rollback warning:** the currently deployed October 7 image has
five anonymously accessible administrative endpoints. Returning
to that image cannot be treated as a security-safe rollback. A
release plan must identify an independently reviewed, authenticated
fallback or safe traffic isolation; neither has been approved here.

Twenty-three operator-canary unit tests ran against fake Docker,
HTTP and pipe fixtures; **no real operator credentials were read**
and **no live production request was sent**.

## Fail-closed cached rollback inventory — actual x1-370 evidence (12:24 EST)

New `scripts/audit_router_rollback_inventory.py` inventories only
explicit, full Docker `sha256:...` image IDs via **read-only**
`docker image inspect` and checks the unchanged live container identity
before/after six anonymous **status-only** admin GETs. It cannot start,
create, stop, restart, attach to, or load any container. It reads no
Docker environment, mounts, credentials, or provider payloads. Its
`--explicit-read-only` opt-in cannot trigger an image swap.

The current x1-370 local inventory found three cached images:

| Immutable SHA-256 prefix | Image creation date | Evidence-based result |
| --- | --- | --- |
| `8e4676c96bd8e037...` | October 25, 2026 | CACHED_UNVERIFIED_HOLD |
| `4023ff9ff6ba511f...` | October 25, 2026 | CACHED_UNVERIFIED_HOLD |
| `f6425386ad566ca5...` | October 25, 2026 | CACHED_UNVERIFIED_HOLD |

The full content-addressed IDs are pinned in the machine-readable
receipt. **Cached existence alone is not an authenticated fallback**:
none of these older images has its installed source, authorized client,
provider/config restoration, independent approval, full no-leak canary,
or rollback drill verified. They were deliberately not started because
the production node was under load and nearly out of swap.

The still-running production image is
`sha256:182d3d3c8aa6b8afbc45bb5094493f74baa58dbb5c0b0b54555b5718f03c1dd8`.
Its port 8088 was bound to **both `0.0.0.0` and `::`**; **five of
six** administrative GETs returned unauthenticated HTTP 200.
The old image is therefore **not a security-safe rollback target**.

Actual read-only receipt on x1-370:
`/tmp/router-rollback-inventory-20261104-1224.json`
SHA-256
`0bdd24280853838d7b7aef31e4716a49ddc9c35e9a32d0b5cbf9e5dd5fcb489a`.
The file is private mode 0600, and the command returned **HOLD,
exit 2**. No containers were changed, and all three image reads used
fixed, full immutable IDs.

The tool refuses mutable Docker tags, duplicate/ambiguous IDs,
missing or re-ordered six-route status evidence, forged `all_denied`
fields, changed container identity during inspection, inconsistent
host-port mapping, and false rollback approval. Its result can **never
become production-ready** merely because an image exists or the
current container is healthy.

**Safe fallback remains UNVERIFIED:** a release must choose and
separately attest either an independently approved authenticated
fallback image plus a tested state/traffic recovery path, or an
explicitly approved external isolation mechanism that blocks
unauthorized access while services are restored. No such rollback
or isolation action was executed in this research cycle.

## Dual-plane isolation-readiness proof — actual x1-370 evidence (12:44 EST)

New `scripts/audit_router_isolation_readiness.py` adds a second,
**read-only and deliberately non-remediating** ingress audit.

Docker port publication and container-to-container bridge membership
are separate paths. Neither a wildcard Docker bind nor an
`Internal=false` network flag proves internet reachability by
itself, and neither proves that the other path is protected.
Changing a host firewall without knowing the actual Docker/NAT
backend and existing client needs could both interrupt legitimate
traffic and leave an unintended path accessible.

The live audit confirmed:
- Running image remains October 7's
  `sha256:182d3d3c8aa6b8afbc45bb5094493f74baa58dbb5c0b0b54555b5718f03c1dd8`.
- Port `8088/tcp` is published on both IPv4 `0.0.0.0`
  and IPv6 `::`. This is a **potential host ingress
  exposure**, not an independently verified external reachability.
- The router is attached to two Docker `bridge` networks:
  `git_default` and `assistx-fleet_default`. Both
  report `Internal=false`. Actual inter-container peer
  access and client allowlist **remain unverified**.
- Five of six status-only anonymous admin reads return
  HTTP 200 from loopback.
- The authorized fleet tool blocked effective firewall-rule
  inspection; no host nftables/iptables, upstream port-forward,
  cloud edge policy or external probe was verified.

The immutable-read-only proof was saved locally:
`/tmp/router-dual-plane-isolation-20261104-1245.json`
(private mode 0600), SHA-256
`31a88fef63d44d2567f0ca4081ac1746cb61c527cfd54eada331f4769132800c`.

**Result: HOLD, exit 2.** No network, Docker, firewall,
operator-credential, container or ingress configuration was
modified or applied. The auditor will not issue `PASS`
or `production_ready=true` from inspect data.

### Proposed release-safe containment pathways (NOT commands to apply)

**Path A — controlled network isolation.** First independently
inventory the effective Docker, host IPv4, host IPv6,
upstream gateway/reverse-proxy and Docker-network client
paths under operator-approved read-only access. Define and
review the minimal required fleet client allowlist. Produce
an approved, peer-reviewed change to restrict *untrusted*
direct host ingress to 8088 and unauthorized Docker-network
peers, keeping authorized internal traffic working. Perform
an independent **nonallowlisted IPv4 and IPv6 probe**
and authorized-client functional checks, with a separate
preapproved safe restoration plan. This path requires an
actual network authority and explicit cutover approval.

**Path B — reviewed hardened image replacement.** Build an
approved, immutable production image with source pins,
first prove the six anonymous admin denials and five
operator-authorized read-only GET responses in staging,
verify ingress and client compatibility on both planes,
and document a rollback to a separately approved
authenticated image **or** independently approved
traffic isolation. The currently running vulnerable
October 7 image is **not an acceptable security-safe
rollback target**.

No proposed isolation path may be declared complete
from one host's wildcard binding, an untested fallback,
a synthetic CI credential, or a locally generated
receipt. A future independent external probe and
authorized fleet-client test are required before the
production gate could change.

The new synthetic tests cover IPv4/IPv6 wildcard
publication, non-internal Docker bridge membership,
false loopback-only assurances, malformed/reordered
network evidence, missing/forged HTTP outcomes,
a changed container during audit, command allowlist
refusal, private receipt integrity and deny-default CLI.
All are offline and do not depend on production credentials.

## Network peer compatibility inventory — actual x1-370 evidence (12:56 EST)

New `scripts/audit_router_peer_compatibility.py` provides a
**read-only, non-authoritative dependency inventory** before choosing
an isolation design. It captures the router's immutable image,
published port and exact attached Docker network names, then reads
*only container names* on each network. It repeats the network
membership and image/start/port inspection to reject concurrent
changes. No Docker `exec`, firewall inspection, token access, logs,
environment inspection, network probe, provider call, container
restart, or network modification is performed.

The actual live inventory found:

| Docker network | Current other container peers | Claim |
| --- | --- | --- |
| `git_default` | `assistx-api`, `auto-assign`, `git-redis-1`, `neo4j` | Four **potential** peers |
| `assistx-fleet_default` | None beyond `git-router-1` | No additional current peers |

Both networks have Docker bridge attachment semantics; the previous
dual-plane audit established `Internal=false`. The names alone do
**not** show these peers use the router's API, that they have
authorized credentials, or that an isolation policy would preserve
their traffic. Host/Tailscale/external clients might not appear as
Docker peers and are **not covered** by this inventory.

The private 0600 receipt:
`/tmp/router-network-peers-20261104-1256.json`,
SHA-256
`7e7e9c59df3571f5a8216e5b799f785ce7c1152095ba0a84d8bdb86d87e9ed44`.

**Result: HOLD, exit 2.** The audit is deliberately incapable of
granting `production_ready`, of authorizing a peer allowlist, or
of promoting image/rollback readiness. Twenty synthetic tests
prove fail-closed behavior for changed image/network membership,
router missing from a claimed network, duplicate/invalid
container names, unreviewed network names, unsafe receipt
destinations, and command invocation without explicit read-only
consent.

Before an approved containment change, an operator must independently
establish each real API client's source network, endpoint and
authorization path, then test necessary authorized traffic and
nonallowlisted denial across the host IPv4/IPv6 publication and
Docker peer planes. The local client inventory alone cannot
approve firewall changes, bridge detachment, or a container cutover.

## November 4 additional fail-closed public-bind gate

The standalone deployed-image provenance verifier no longer treats `--allow-public-bind` as sufficient authorization for `0.0.0.0` or `::` publication. The legacy argument is accepted only for invocation compatibility; requesting it forces a HOLD even if the six anonymous admin canaries return 401. A preliminary source/image inspection can now pass only with explicit loopback-only host bindings. Separately approved host/firewall or Docker-peer containment must be established by independent network evidence and the normal release process, not by a CLI override. Synthetic negative tests cover both wildcard bind and a loopback binding with the override requested. This is research code, not an assertion about production port state.
