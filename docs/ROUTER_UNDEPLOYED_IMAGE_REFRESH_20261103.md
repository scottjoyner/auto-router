# Undeployed Auto-Router security image: refreshed evidence

**Draft research / no production deployment / no automatic router cutover.**

This branch stacks on [Auto-Router PR #41](https://github.com/scottjoyner/auto-router/pull/41)
at commit `37237a0c481c9ffa33eb3f1cfe6dfe8d505b17ad`.
Only the candidate-image workflow and this review note differ from its parent.
The effective provider and inherited admin-auth source, Dockerfile and
production Compose configuration are **unchanged**.

## Why a new hosted candidate is necessary

The October 11 GitHub Actions candidate build/isolated load test passed at
[workflow run 38168687051](https://github.com/scottjoyner/auto-router/actions/runs/38168687051).
Its then-built immutable image ID was
`sha256:f538dd649b618fdc25ae4994b07b978f1cb31acd1dad112d676be2fc1dec777b`.
That successful historical result is not a deployable candidate today:
the workflow artifact was retained only three days, its current run-artifact
listing is empty, and the exact image ID is not present on the x1-370 host.
The currently running unpatched image is
`sha256:182d3d3c8aa6b8afbc45bb5094493f74baa58dbb5c0b0b54555b5718f03c1dd8`.
**Never substitute a mutable `git-router:latest` tag for either pinned image.**

## Narrow refreshed evidence workflow

The PR-triggered workflow is identical to the previously validated workflow
except the permitted **draft branch** name, a descriptive display name, and
artifact retention increased from **3 days to 14 days**.

The hosted runner must check out the exact PR head with no persisted
GitHub credentials, validate source identity, resolve a digest-pinned
upstream Python image, and build a locally labeled immutable candidate.
It then executes the actual `auto_router.secure_live:app` route dependency
and anonymous-denial tests **inside an isolated network-disabled container**,
exports the image as an archive with SHA-256 and an immutable image ID,
and uploads the short-lived reviewed archive. A second independent hosted
job must restore the archive, verify SHA-256 and metadata, prove the real
Compose override does not change unrelated services, load the image on
the disposable runner, and re-run route/auth tests.

**Neither job targets a fleet node, starts a real router, acquires production
credentials, runs a live provider probe, restarts Docker, or claims a Task.**
It is a hosted build and disposable restore of the candidate only.

## Release gates still held

- The new hosted workflow must finish SUCCESS on the actual exact PR head;
  document its new image ID, archive digest, artifact ID, TTL and download
  evidence. Never infer this from the October run.
- Independently obtain a recoverable, immutable **rollback** image and
  verify live Compose paths, secrets custody and actual client-first rollout
  order (Auto-Assist #288, #538 and #532, then router #41 and optional #123).
- The deployed router still returns anonymous HTTP **200** on
  `/admin/providers` and the optional authenticated effective route is not
  deployed. Require local and two independent Tailnet-node anonymous
  **401**, authorized sanitized GET **200**, and a tested rollback sequence
  **after** any separately approved supervised cutover.
- The canonical Task projection is expired, work supply has zero approved
  slots, and graph RBAC/custody are independently on HOLD.
- The hub is dangerously low on swap; do not build, swapoff, restart Neo4j,
  load models, or attempt a cutover on x1-370 as part of this PR.

A successful hosted image build is **not** permission to promote or to
alter production credentials, services, firewall rules or Task admission.
