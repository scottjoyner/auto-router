# Agent-job read boundary: separate draft security slice (2026-10-11)

**HOLD / NOT DEPLOYED.** This is stacked on draft Auto-Router PR #41,
not a change to its security-only candidate image or current production.

## Evidence from running `auto_router.secure_live:app`

A metadata-only inspection of the actual mounted FastAPI route table found:

- `POST /jobs/agent` has `require_admin` already.
- `GET /jobs/agent/{job_id}` and
  `GET /jobs/agent/{job_id}/artifacts` **do not**.
- The configured `ExecutorInferenceAuthMiddleware` only examines selected
  **POST** requests; it cannot protect these GETs.
- The GET handlers fetch job records and artifact metadata for supplied job
  IDs, with no in-handler owner authorization.
- Separate unauthenticated dashboard and metrics routes exist and were not
  changed. The contents, intended audience and external caller dependencies
  of those pages require **separate** security review.

No actual job ID, job record, artifact body, URL credential, personal data,
or metadata response body was fetched. No successful disclosure is claimed.
This is a concrete authorization gap **if** a job ID becomes known.

## Narrow proposed patch

Add `Depends(require_admin)` to just the two GET handlers. The existing
`require_admin` path is already used on POST job creation and protected
admin routes, and rejects absent/incorrect keys with 401.

Synthetic tests verify that neither `state.agent_jobs.get` nor
`state.agent_jobs.artifacts_for` is called when the request lacks a
valid credential; authorized requests for fake nonexistent IDs return 404.
A Docker integration smoke verifies both GET paths are protected in the
actually mounted `secure_live` ASGI app and return anonymous 401. The
synthetic tests never start the service lifespan or touch real job state.

## Caller acceptance before merge/deploy

1. Inventory all scripts, operator pages, clients and automated processes
   that read job detail/artifacts. A browser cannot supply
   `X-Admin-Token` via normal navigation; never place the admin secret in
   frontend JavaScript, an URL, cookies or logs as a workaround.
2. Decide whether job records require per-owner access instead of the
   shared router admin token. This narrow fix is a **temporary least-change
   privileged boundary**, not a two-user owner-binding design.
3. Check health, route/model discovery and inference/data plane remain
   unaffected. Accept the exact reviewed source SHA in a disposable
   packaged `secure_live` image before any authorized deployment.
4. Verify authorized operator GET 200 for a *synthetic* owned job and
   authorized fake-job 404; verify wrong/absent token 401 before lookup.
   Do not use live customer or user records for initial tests.
5. Keep independent P0 gates: [PR #41](https://github.com/scottjoyner/auto-router/pull/41)
   admin inheritance fix, [router issue #40](https://github.com/scottjoyner/auto-router/issues/40),
   [AssistX #156](https://github.com/scottjoyner/auto-assist/issues/156) historical
   credential custody, and [AssistX #299](https://github.com/scottjoyner/auto-assist/issues/299)
   shared-token transport.

**No production container, firewall, token, Docker network, image or job record
was modified to produce this review.**
