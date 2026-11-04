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
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. python3 -B \
 scripts/verify_deployed_admin_release_identity.py \
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
