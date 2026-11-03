# Starred witr audit: distinguish a live port from a compatible inference model catalog

**2026-11-03 · read-only fleet probe · production NO-GO**

## Candidate reviewed

The full public star list under `scottjoyner` remained **1,068 repositories**
(10 full pages + 68 on page 11). The most actionable new operational tool
was [pranshuparmar/witr](https://github.com/pranshuparmar/witr), an
Apache-2.0 CLI for tracing which process, ancestor or systemd scope owns a
listener. It was evaluated at upstream source commit
`9e54a0507a4b3025fe8c3ecb329c7cb8da627995` and public release
**v0.3.4**, binary SHA-256
`d99098c61002f9b8a5337977d527dbe8ee29453f2a62c531038ea2749ea738eb`,
matching GitHub's release checksum. This digest is a publisher-hosted
checksum, **not an independent source-build or binary signature**.

The verified executable was temporarily copied over **already authorized,
host-key-checked SSH** to `xwing` and `joyner`. It was run without root,
without environment-variable or command-line argument disclosure, and
removed by each remote shell's exit trap. No packages, systemd units,
listeners, model endpoints or firewall rules were changed.

## Material finding: port ownership plus response schema

Read-only `witr --port ... --json` was reduced to process ID, source
manager/type and warning count. Witr identified the xwing port 1234
listener under a Chromium desktop scope, but its returned process
`Exe` field was empty in this context. Cross-checking `ss -ltnp`,
`ps -p` and `/proc/PID/exe` established that the **actual listening
process was `lm-studio`**, PID 885908 at time of inspection, listening
on `0.0.0.0:1234`.

On Joyner, **witr missed the port owner**; `ss` revealed more than one
binding at port 1234, including loopback, LAN and tailnet addresses.
The loopback listener's PID 1400803 was actually
**`llama-server`** at inspection. These PIDs are volatile and should
not be used as stable identities or hardcoded automation references.
A non-root `witr` negative result is **not proof of an absent process**.

Read-only HTTP GET responses on the hosts:

| Node | Native `/api/v1/models` | Native `/api/v0/models` | `/v1/models` | Actual semantic meaning |
| --- | --- | --- | --- | --- |
| xwing | 200, 16 records | 200, 17 records | 200, 17 OpenAI-style `data` records carrying `id` | LM Studio: two native-measured resident instances, remaining IDs just catalog visibility |
| Joyner | 404 | 404 | 200, one `models` record with `name`, `model`, `digest`, `size`, `type`; **no `data[].id`** | HTTP reachable but **not** a verified OpenAI-compatible model catalog |

The third response is **Ollama-shaped**, even though `ss`
identifies the owning process as `llama-server`. It is **not**
a claim that the running process is Ollama, and it is not proof
that the server cannot generate text through some other endpoint.

## Concrete Auto-Router bug and narrow fix

Auto-Router's existing
`src/auto_router/fleet_task_dispatcher.py`
used the same permissive `_inventory_rows()` helper for
LM Studio native APIs and the **OpenAI** `/v1/models` API.
It accepted `models` as a top-level key and `model` as a
fallback model identifier. This caused Joyner's
`{"models":[{"model":"..."}]}` to be reported as one
OpenAI-compatible advertised model, despite lacking
an OpenAI `data[].id` record.

**This branch is stacked on the verified, still-draft**
[Auto-Router PR #115](https://github.com/scottjoyner/auto-router/pull/115),
base SHA
`4a61e3a1fab6c8fc255898aa66d16e2206969d98`.
It retains PR #115's strict native residency proof.
No production state or live provider config is changed.

Added `_openai_compatible_rows()` for the compatible
endpoint **only**. It requires a JSON object with
`data` as a list, containing only dictionaries whose
`id` values are nonempty, bounded, control-free,
unique **strings**. Optional `object` markers must
be `list` (outer) / `model` (row); their
absence remains permitted for compliant
compatible implementations. It rejects mixed
`data`+`models` top-level schemas, model ID
in `name` or `model` only, invalid types,
duplicate IDs, control chars, whitespace and
oversized catalogs.

When the compatible endpoint responds HTTP 200
but with an incompatible schema, the scanner
records `v1/models: incompatible-model-schema`
**without copying untrusted HTTP body content into errors**.
Its model IDs are excluded from `all_models` and
`loaded_models`; if no separate valid native
API is available, `online=false` denotes
*no valid model catalog*, **not network unreachable**.
If a native LM Studio API provides valid independent
residency, that native observation remains available
even when the compatible endpoint is invalid.

This is an identity/schema fix, not inference
admission. HTTP status alone cannot authorize
model loading, generation, tool calling, security
policy, task dispatch or signed receipt custody.

## Exact red/green and live validation

The new `tests/test_witr_openai_endpoint_schema.py`
suite on **untouched** PR #115 head reproduced
**31 failed, 5 passed** (including the live Joyner-like
shape). Following the narrow fix:

- **127 passed, 4 intentionally skipped**
  in a combined local suite covering new
  protocol-schema tests, the earlier native
  residency tests, fleet discovery/security,
  live model routes, placement mutation safety,
  fleet report/health and authority contracts.
- Ruff lint and Ruff format-check passed
  on the two touched Python files.
- One preexisting Starlette/httpx deprecation
  warning, not a test failure.

Final read-only `_probe_provider()` comparison
from `x1-370` using already configured **tailnet
FQDNs**, no credentials and HTTP GET only:

| Node | `online` (valid catalog) | Observed IDs | Native resident | Authoritative | Endpoint statuses |
| --- | --- | ---: | ---: | --- | --- |
| xwing | True | 17 | 2 | True | `api/v1:ok:16`, `api/v0:ok:17`, `v1:ok:17` |
| Joyner | **False** | **0** | **0** | **False** | `api/v1:http-404`, `api/v0:http-404`, `v1:incompatible-model-schema` |

This shows that the fix rejects the demonstrated invalid
catalog without losing a genuine native LM Studio inventory.

## Deployment boundary and next gate

**DRAFT / production HOLD.** No production
discovery process, routes, models, ports, credentials,
Graph/SGNC traces, firewall or services changed.
No new generation was run, and **zero token
throughput was measured** in this iteration.

Joyner's configured LM Studio provider remains
**disabled** pending the existing authenticated
route/protocol/owner cutover gates. A correct next
step is to identify which **actual** llama-server
API contract is supported by that build and verify
a separate explicitly configured adapter under
approved credentials; not relabel `models[].model`
as an OpenAI `data[].id` or silently enable
a route. End-to-end admission also needs a bounded
successful generation, valid tool-call outputs,
latency and signed token receipts, followed by
existing SGNC custody and operator-policy gates.

Witr itself is a useful **on-demand operational
diagnostic**, but this evidence does not justify
installing a persistent cross-fleet agent, exposing
a new API surface, running it with root, or
depending on its negative result without `ss`
cross-checks. Further code-context tooling
(tgrep/Ripwire) also remains research-only
until downstream task correctness and real model
tokens are measured.
