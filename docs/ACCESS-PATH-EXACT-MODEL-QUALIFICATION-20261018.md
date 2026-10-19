# Auto-Router | exact-model access-path qualification

October 18, 2026. Code-only correction in an isolated branch;
no router reload, provider addition, admission or model promotion.

## Defect during Joyner outage response

RuntimeAccessPathSelector in access_paths.py considered any HTTP response
with status below 500 a valid route to a selected physical runtime.
HTTP 302, 401, 403, 404, and 429 therefore counted as reachable.
HTTP 200 containing an empty or wrong-model catalog also passed.
None of these proves the selected model can serve the request.

## Fail-closed remediation

The default path probe now:
- Disables redirects and requires HTTP 200.
- Requires a JSON object with OpenAI-style data list and exact expected
  provider_model identity in one entry's id property.
- Denies catalogs over 64KiB, malformed/empty response bodies, wrong
  model IDs, expired/errored HTTP paths and timeout failures.
- Retains bounded HTTP connect/read/write/pool timeouts.
- Makes no inference POST, secret checks, model load or router mutation.
- Leaves injected test-only/custom probes unchanged.

The path selector still uses provider-approved URLs only. A successful
catalog check DOES NOT prove residency, generation, source-admitted
runtime projection, per-user ownership, current capacity or fencing.

## Evidence and rollout boundary

Six relevant Auto-Router test suites, including newly added negative
HTTP 3xx/401/403/404/429 cases and exact-model mismatch cases, pass
50/50 in the isolated Auto-Router virtualenv. No physical routed request
or authentication change occurred.

This is NOT merged or deployed. Nonstandard model-catalog formats might
now be denied; operator review of all approved provider adapters is
needed before any production change. Rejecting an unsupported path with
503 is preferable to selecting a path that cannot serve the selected
model.

Before promotion, prove a live fresh canonical AssistX runtime/model
projection, per-user authenticated generation and receipt fences,
task-specific compatibility, node-loss rollback, and 72-hour soak.
Review Lenovo CPU direct listener bound to wildcard :1234; catalog
is reachable over LAN without credentials. Do not infer external POST
authorization without separately verifying the gateway boundary.

Joyner GLM-5.3 production admission remains HOLD. Lenovo LFM CPU is
only a restricted degraded-mode research candidate.

## Model-switch cache boundary — additional safety test

An existing per-runtime cache stored the last approved transport without
remembering which provider model was verified. Even with the new exact
model catalog probe, model A could qualify the LAN path and model B
could reuse that cached path for up to 30 seconds without a model-B check.

The patch now binds each cached choice to the exact provider_model.
A cache hit is valid only if both runtime identity and model identity
match the current candidate, within the bounded TTL. Switching models
re-probes an approved path even if the transport was recently healthy.
A failed model-B probe leaves model-A's prior selection intact, but
cannot authorize B. This does not change runtime capacity accounting:
physical runtime admission is still shared.

Additional hermetic tests validate a missing model B is denied despite
model A's cached HTTP200, and a successful A-to-B-to-A switch rechecks
each distinct model. Six adjacent suites now pass 52/52.

## Two-user authenticated authority still unverified

Source inspection of Auto-Router's ExecutorInferenceAuthMiddleware and
AssistX's claim-status endpoint found that the token/fence tuple is
task_id, claim_id, agent_id and projection_generation. There is no
independently verified human owner_id in the inspected claim-status
response, and Auto-Router idempotency groups an explicit key by a
hash of the presented bearer. These are valuable task-execution
boundaries, not proof of distinct authenticated human/tenant owners.
A two-principal gateway acceptance test cannot be reported as passed
until the producer contract and consumer verifier expose and check
canonical owner binding with actual separately authenticated users.

Live routing remains untouched, and this code stays draft/review only.

## Full-suite result and independent live-drift reproduction

Full non-integration suite on this isolated review branch (Oct 18):
447 passed, 9 skipped, 1 failed. The sole failure was the deliberate
live drift assertion in test_provider_registry_drift.py. During this
run the x1-370 provider advertised a Qwen 0.8B distilled entry that
its live catalog did not serve. In an unmodified auto-router checkout,
the same exact test independently failed, reporting a different
missing x1 model alias (toolcall-v5-3b-combined-r2). The live runtime's
advertised/served set can change between samples, but the failure class
is present without this patch; the drift test should NOT be weakened.

This review branch does not update the x1 provider registry, load
either missing model or renew any stale admission approval. Fixing live
registry drift requires a separately authorized inventory and model
owner decision.

## Genuine two-owner claim contract gap

The present AssistX issuer and Auto-Router fence use task_id, claim_id,
agent_id, projection_generation, and a signed short-lived executor token.
The inspected AssistX claim-status response does not carry a canonical
human/tenant owner identifier, and the Router cannot verify that a
claimed task belongs to one of two separately authenticated human users.
Moreover, the local executor attempt-count ledger is process-local;
these tests do not validate replay budgeting across worker restarts.

Any accepted human-owner-bound fallback MUST add a trusted owner
identity to the issuer and claim-status authority, bind it to a distinct
authenticated gateway principal, reject stale/revoked owner scopes,
and produce persistent per-owner execution receipts in a real
two-principal authenticated integration test. Do not interpret two
synthetic prompt labels or two executor task IDs as that acceptance.
