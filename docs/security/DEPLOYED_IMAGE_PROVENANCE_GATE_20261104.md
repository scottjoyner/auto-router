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
