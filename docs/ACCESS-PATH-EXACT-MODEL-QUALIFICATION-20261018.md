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
