# Fleet cost shadow — authority and evidence contract

**2026-10-11 • Research only • No real-data ingestion, dispatch or production authority.**

Parent [draft auto-router PR #49](https://github.com/scottjoyner/auto-router/pull/49); tracking [issue #43](https://github.com/scottjoyner/auto-router/issues/43).

## Why

The 48 actual xwing Von inferences on 12 fictional two-eligible cases did not beat even a crude deterministic heuristic on known numerical latency and energy objectives. This slice supplies a transparent **fictional-only** cost ranking algorithm rather than making Von/QSIV a second scheduler. It never executes inference, starts services, loads weights, contacts the network, ingests NAS data or authorizes a real request.

The Python module at experiments/fleet_cost_shadow.py **only** accepts schema fleet-cost-shadow-v1 with kind fictional-only and will reject a purported live/verified request. The example is experiments/fixtures/fleet_cost_fictional.json. All request/runtime/telemetry assertions in the input are **invented and unverifiable**; no issuer signature or trusted auth interface exists in this prototype.

## Required authoritative producers for a FUTURE real adapter

| Input | Required producer / proof | Not sufficient |
|---|---|---|
| Request owner and request ID | Server-authenticated owner, minted binding and owner-bound receipt; auto-router issue #37 remains open | Client-provided IDs/headers |
| Node, physical process and model revision | Canonical AssistX fleet + host process observation + exact version/digest | LM Studio Link localhost, /v1/models inventory |
| Effective configured provider | Authenticated effective in-memory catalog, proposed by auto-router draft PR #41 and AssistX draft PR #288 | Mounted YAML, catalog AVAILABLE, provider alias |
| Health, capacity, admitted runtime and lease | Canonical authorization/reservation source with owner and request binding and revocation freshness | Stale healthy discovery or queue length alone |
| Performance/quality data | Source-custodied completed request receipts tied to exact running process, model, task and subject | An old process, different model or model estimate |
| Power, battery and offline dependencies | Host/UPS measurements, reviewed hardware dependency graph, Govee outlet samples, switch/router/Tailscale survival | Single plug watts or battery percentage |
| Runtime execution | Independent signed/validated provider execution witness | Local SHA256 report, a server health response or model name |

Neither draft admin endpoint is live proof of model residency, physical host or admission. The router README keeps AssistX authoritative for runtime identity, task assignment, allocation and recovery. This experiment does not widen that boundary.

## Research contract enforced

- Strict bounded allowlist, 64-runtime/telemetry limit, no raw prompt/network/secret fields. Duplicate runtime IDs, **node-scoped physical process identities** (node ID + process ID), globally duplicated reservation IDs and telemetry rows reject the **whole input**. Identical local PIDs on different hosts are not automatically the same physical process.
- An invented runtime passes the eligibility screen only when owner, request, model, task, LOADED state, HEALTHY state, free slot and active lease all match. AVAILABLE and UNKNOWN are not LOADED.
- Performance telemetry must match the **same runtime, model, task and process**. This prevents a model swap or service restart inheriting old estimates. At least eight samples are required. Quality lower bound, deadline check and freshness must pass for **every eligible** runtime; otherwise return EVIDENCE_INCOMPLETE and no partial rankings.
- Fictional as-of time: authority/runtime freshness 30 sec, measured performance freshness 120 sec, power freshness 60 sec, authority expiry within 30 sec. These are prototype limits, **not trusted clock or released production policy**.
- One objective at a time: elapsed-time proxy in milliseconds or energy proxy in joules. Estimated time = TTFT_p95_ms + 1000 × P90_output_tokens / token_rate_p10. Estimated joules = time_ms × watts_p95 / 1000. Combining marginal quantiles gives a **conservative proxy, not a statistically certified end-to-end p95 or SLA**. Watts must be interpreted cautiously; no real incremental power validation was performed.
- Optional fictional outage survival uses a 70% reserve factor, remaining battery watt-hours and full dependency watts; every eligible candidate must have current observations. It **does not** establish real UPS/router/switch/tailnet independence or survivability. An unresolved dependency blocks ranking.
- Sort on **unrounded** objective cost, then stable runtime ID. Rounding is for display only.
- Possible statuses are NO_ELIGIBLE_RUNTIME, EVIDENCE_INCOMPLETE and RANKED_SYNTHETIC_ONLY. All outputs set authority_to_dispatch=false, physical_execution_attested=false and production_promotion_approved=false. They cannot supply execution tokens.

## Future integration safety gate

**Authentication cannot be inferred from JSON fields or hashes.** A live adapter requires an out-of-band validated evidence interface for server-minted owner, request, process, model digest, source signature, and current reservation state. It must support revocation and compare-and-admit atomicity. A read-only ranking can expire between observation and dispatch; a separate executor must revalidate and atomically reserve the runtime before any work. This experiment intentionally has no such code.

A real high-confidence provider-catalog GET and the expected AVAILABLE-versus-LOADED distinction are useful inventory context but cannot mint task authorization. Separate receipt, physical loaded-state, lease and task-quality evidence are still blockers.

The full hardware dependency graph matters for offline ranking: an on-battery laptop might survive while a NAS, DNS resolver, Govee power outlet, WAN router, USB controller or Tailscale exit node fails. One power measurement cannot prove the requested survival horizon.

## Run offline

    python3 -m unittest discover -s tests -p 'test_fleet_cost_shadow.py' -q
    python3 experiments/fleet_cost_shadow.py experiments/fixtures/fleet_cost_fictional.json

The fully fictional example has two eligible runtimes. A is faster; B has lower estimated energy consumption. Test mutations cover stale authority, wrong owner/model, AVAILABLE-only models, duplicated physical identity, stale process measurements, missing quality, deadline exceedance, and offline survival.

**Next:** independently authenticated source adapter (separate draft), real host+Govee dependency evidence and per-user completed physical execution receipts, then a read-only replay of measured real cases. No live rollout, operator or iPhone exposure, NAS writes or merge approval from this synthetic slice.
