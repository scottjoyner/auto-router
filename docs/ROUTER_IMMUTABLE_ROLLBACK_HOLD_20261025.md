# Auto-Router P0: reversible cutover evidence and NO-GO checklist

**Status:** DRAFT / research-only / no production changes authorized.
**Scope:** `scottjoyner/auto-router` PR #82, issue #40.
**Observed baseline:** October 25, 2026. Do not reuse these observations as
current-state facts after any restart, image update, host migration, credential
rotation, Tailscale policy change, or Docker/Compose reconfiguration.

## Confirmed facts

- x1's production `git-router-1` remains on immutable Docker image ID
  `sha256:182d3d3c8aa6b8afbc45bb5094493f74baa58dbb5c0b0b54555b5718f03c1dd8`.
- Compose references **mutable** `git-router` (`git-router:latest`). The
  current tag points to that image, but retagging/importing could change the
  identity Compose launches next. A matching tag is not rollback custody.
- Cached old-image metadata and a current mount/network compatibility check
  exist, but **no independently hashed, independently custodied immutable
  archive and no completed restore rehearsal** exist.
- The old image has known anonymously accessible administrative GET routes,
  including `/admin/providers` and `/admin/circuits`. Rolling back to it
  without reviewed containment **reintroduces this P0 exposure**.
- Exact-source CI verifies the replacement's synthetic admin authentication,
  but does **not** qualify physical production credentials or real inference.
- Production AssistX API, Worker, and Hermes currently agree with the
  Router admin token and point at `host.docker.internal:8088`; that is
  configuration and old-image baseline evidence, not replacement acceptance.

## Safe read-only metadata gate

Use `scripts/inspect_router_immutable_rollback.py` with:

- `--compose-file`: the original read-only Compose path from the live
  Router's exact Docker project metadata, never a synthetic/replacement file.
- `--expected-running-image`: a separately verified *immutable* full
  Docker image ID identifying the current, pre-cutover deployment.
- `--expected-candidate-image`: the full immutable candidate image ID
  certified by the exact GitHub CI archive (not an image tag or truncated SHA).

This tool performs only fixed `docker inspect` / `docker image inspect`
commands and bounded Compose parsing. It compares the image pins, current
mutable-tag mapping, container status, restart policy and required network,
port, and mount access contracts. It never runs Docker restart, save, load,
tag, compose up/down, inference, credential printing or an HTTP request.

**The exit code is always nonzero and the verdict is always HOLD.** A clean
metadata comparison cannot confer release approval. A missing candidate image
on x1 is recorded as an explicit precondition failure; do not silently
substitute an Xwing-cached image or any old CI image.

## Operator-scoped post-cutover readback (separate acceptance, never deployment)

`scripts/verify_postcutover_router_auth_readonly.py` is an opt-in **GET-only
post-cutover acceptance** tool, not a deployment script, rollback command, or
authorization to turn on providers. It requires the full immutable candidate image
ID, rejects the known old vulnerable image, confirms the new image is *already
running*, and refuses all network and Docker-exec probes without the explicit
`--authorize-readonly-check` flag. Without authorization or on the current old
production image it exits **2/HOLD** without contacting the Tailnet or clients.

After a separately approved and completed cutover, the operator may supply the
full newly verified candidate image and explicitly authorize only the bounded
readback. The checker performs:

- Via strictly host-key-checked SSH to **Xwing**, direct HTTP GET status-only
  reads of **four deliberately passive** administrative endpoints over the
  x1 Tailnet address. Each must return 401 with no credentials. The more
  active `/admin/providers/health` endpoint is deliberately *excluded*: it could
  cause downstream provider health probes if authentication were broken.
  Complete 20-operation protection has a **separate** disposable CI gate.
- Two public GET status checks (`/health` and `/v1/models`) must return 200.
  No HTTP bodies are read or returned.
- From inside each running, allowlisted AssistX API, Worker and Hermes container,
  three authorized GET status checks with its **own in-memory** admin token must
  return 200 (`/admin/providers`, `/admin/context`, `/admin/admission`).
  The token is never passed through host shell arguments or printed.
- Image identity is read back repeatedly between remote and client requests
  so an unobserved mid-check Router replacement causes HOLD. This is not
  atomic fencing and cannot substitute for operator coordination.

The script only accepts a pinned image with `sha256:` and 64 hex digits; it
**does not independently verify an image signature** or authorize the initial
deployment. Its strongest success verdict is
`READONLY_POSTCUTOVER_AUTH_CHECKS_PASS_RELEASE_HOLD`; production authorization,
rollback proof, trace custody, safe failover, and provider effects stay **false**.
Review and approve any production use separately. Never use the old image as
a newly approved candidate. The readback contains only sanitized boolean
verdicts and blocker codes.

## Requirements before considering a production cutover

1. **Independent custody of the old release.** An authorized operator must
   preserve and independently checksum the old exact image and verify its
   digest, extraction/import, provenance, custody, and recovery path on a
   disposable host. A mutable Docker tag and a local image cache do not
   meet this requirement.
2. **Safe rollback, not just old-code restore.** Demonstrate that a rollback
   can restore required volumes, schema compatibility, authentication
   configuration, ports, networks, and client connectivity **without reopening
   the known old admin exposure**. This likely requires independently reviewed
   network containment or a separately qualified security-patched rollback
   candidate. An operator must approve any such containment; do not blindly
   close port 8088 while consumers depend on it.
3. **Exact replacement artifact.** Verify GitHub source commit, exact OCI
   archive byte count/SHA256, immutable image ID, and a trusted signature/
   independently administered custody mechanism. The artifact must match
   current PR HEAD; old CI artifacts must not be reused after a new commit.
4. **Restricted disposable acceptance.** Require all 20 mounted admin
   operations to deny missing/wrong credentials; allow legitimate operator
   reads; prove provider and prompt paths do not disclose credentials or run
   uncontrolled effects; keep model dispatch and provider calls disabled.
5. **Real-client preflight and owner-bound effects.** Recheck the real AssistX
   token/URL/host-gateway parity and prove the replacement works for approved
   clients. Strict shared-provider quota ownership, revocation, fencing,
   cancellation, stream behavior and trace custody are separate safety gates.
6. **Controlled switch and contingency.** Only an explicitly authorized,
   bounded deployment may swap the production image after checkpoints,
   change-window coordination, rollback readiness and independent safety
   reviews. Record start/end immutable IDs, source generation, checksums,
   client-facing routes, and independent read-back. Avoid running concurrent
   migration/claim writers.
7. **Post-cutover authentication and rollback drill.** From an approved
   off-host Tailnet client, test missing and incorrect admin credentials,
   legitimate operator reads, inference-client compatibility, timeouts and
   failover. Verify rollback under a separately scoped, authorized drill
   without weakening the security boundary. Only then reconsider issue #40.

## Automatic HOLD / rollback triggers

Stop and preserve evidence if the image SHA or Compose lineage drifts; any
admin route returns 200 without valid credentials; the resource-owned quota
or token/revocation fence is absent; authorized clients fail unexpectedly;
response bodies or secrets leak into logs; any backup is unverified; or the
rollback target would resurrect uncontained P0 exposure.

**No autonomous deployment is authorized by this checklist.** Keep Router
PR #82 draft, P0 issue #40 open, and provider dispatch/autonomous harvesting
inactive until physical proofs and operator authorization are recorded.
