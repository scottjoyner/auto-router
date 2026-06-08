# Swarm Unification Backlog

This is the execution backlog for turning providers and model endpoints into first-class swarm elements across the router / assignment / execution / graph stack.

Primary goal:
- Make providers, live model endpoints, route signals, workers, and graph context behave like one coherent swarm inventory.

Reference docs:
- `docs/SWARM_UNIFICATION_CHECKLIST.md`
- `docs/SWARM_UNIFICATION_EXECUTION_PLAN.md`
- `docs/NEO4J_ASSISTX_INTEGRATION.md`
- `docs/AGENTGATEWAY_INTEGRATION.md`
- `docs/IMPLEMENTATION_PLAN.md`

---

## Backlog structure

P0 = must do first
P1 = high value, next after P0
P2 = important follow-up once core paths are stable

Each ticket below includes:
- goal
- files to patch
- suggested verification commands
- success criteria

---

## P0 — Contract normalization and live inventory alignment

### P0.1 Canonicalize swarm identity rules

Goal:
- Make provider IDs, provider-scoped model IDs, node IDs, and signal IDs stable and canonical everywhere.

Patch targets:
- `src/auto_router/context.py`
- `src/auto_router/model_registry.py`
- `src/auto_router/live_models.py`
- `src/auto_router/signal_registry.py`
- `src/auto_router/route_events.py`
- `src/auto_router/route_event_patch.py`
- `src/auto_router/ops_dashboard_routes.py`
- `src/auto_router/templates/fragments/dashboard_summary.html`

What to change:
- merge provider aliases into canonical provider records
- merge model aliases into provider-scoped model IDs
- keep node IDs stable and durable
- surface canonical IDs in route events and dashboards

Verify:
- `pytest tests/test_context_services.py tests/test_live_models.py tests/test_route_events.py`
- `curl -s http://localhost:8088/admin/live-models | jq`
- `curl -s http://localhost:8088/admin/context | jq`
- `curl -s http://localhost:8088/admin/ops/summary | jq`

Success criteria:
- the same provider/model appears with the same ID in context, registry, ops, and route events
- duplicate aliases no longer look like separate swarm elements

---

### P0.2 Make live inventory the source of truth for visible swarm state

Goal:
- Ensure provider/model live discovery is projected into the same snapshot used by routing and dashboard rendering.
- Ensure AssistX context projection remains the preferred live source of truth for the swarm snapshot, with bootstrap only as a fallback/degraded path.

Patch targets:
- `src/auto_router/main_live.py`
- `src/auto_router/live_model_routes.py`
- `src/auto_router/model_registry.py`
- `src/auto_router/context.py`
- `src/auto_router/ops_dashboard_routes.py`
- `src/auto_router/main.py`
- `src/auto_router/preflight.py`
- `docs/OPERATOR_RUNBOOK.md`

What to change:
- hydrate the live registry first, then reproject context
- keep dashboard summary counts tied to the same live snapshot
- make refresh endpoints rehydrate both registry and context
- ensure `/v1/models` reflects the same inventory that dashboard summaries report
- surface a degraded warning when AssistX projection cannot be loaded and the router falls back to bootstrap
- keep the auto-assist / AssistX task lifecycle, backlog dry-run, and event sink endpoints in the same contract review

Verify:
- `pytest tests/test_live_models.py tests/test_ops_dashboard_routes.py tests/test_preflight.py`
- `curl -s http://localhost:8088/v1/models | jq`
- `curl -s http://localhost:8088/admin/live-models | jq`
- `curl -s http://localhost:8088/admin/context | jq`
- `curl -s http://localhost:8088/dashboard | head`

Success criteria:
- dashboard counts and API counts match
- live endpoint changes are visible without waiting for a restart
- if AssistX projection is unreachable, operators see a degraded/fallback signal instead of assuming bootstrap is authoritative

---

### P0.3 Enforce local-only and privacy hard stops everywhere

Goal:
- Ensure cloud-backed providers never receive local-only/private requests.

Patch targets:
- `src/auto_router/policy.py`
- `src/auto_router/providers.py`
- `src/auto_router/main.py`
- `src/auto_router/main_live.py`
- `src/auto_router/gateway.py`
- `src/auto_router/gateway_config.py`
- `tests/test_policy.py`
- `tests/test_agentgateway_privacy.py`
- `tests/test_agentgateway_fallback.py`

What to change:
- treat `metadata.local_only`, `local_only`, `allow_cloud=false`, `priority=local_only`, and `auto/private` as hard stops
- keep sensitive markers local-only unless explicitly allowed
- ensure gateway fail-open never escalates private requests to cloud

Verify:
- `pytest tests/test_policy.py tests/test_agentgateway_privacy.py tests/test_agentgateway_fallback.py`
- `curl -s http://localhost:8088/health | jq`
- smoke one `auto/private` request once a safe local model is available

Success criteria:
- private/local-only requests never reach cloud-backed routes
- privacy enforcement is consistent across direct and gateway paths

---

## P1 — Route signal and graph/provenance closure

### P1.1 Emit and hydrate route signals on success and failure

Goal:
- Make route decisions durable and immediately visible in context.

Patch targets:
- `src/auto_router/route_events.py`
- `src/auto_router/route_event_patch.py`
- `src/auto_router/signal_registry.py`
- `src/auto_router/ledger.py`
- `src/auto_router/main_live.py`
- `src/auto_router/ops_dashboard_routes.py`
- `tests/test_route_events.py`
- `tests/test_ledger.py`

What to change:
- emit route signals on both success and failure paths
- hydrate signals back into live context immediately after emission
- keep route events metadata-only
- include canonical provider/model/node IDs in the signal payload

Verify:
- `pytest tests/test_route_events.py tests/test_ledger.py`
- `curl -s http://localhost:8088/admin/ops/summary | jq '.context_route_signal_summary'`
- `curl -s http://localhost:8088/admin/context | jq '.signals'`

Success criteria:
- route signals are visible in both the live context and ops summary
- failures and successes are distinguishable and stable

---

### P1.2 Make AssistX write-back metadata-only and idempotent

Goal:
- Persist route provenance without prompt bodies or response bodies.

Patch targets:
- `src/auto_router/event_outbox.py`
- `src/auto_router/event_dispatcher.py`
- `src/auto_router/main_live.py`
- `src/auto_router/route_events.py`
- `src/auto_router/ledger.py`
- `docs/NEO4J_ASSISTX_INTEGRATION.md`

What to change:
- make route provenance payloads metadata-only
- ensure idempotency keys exist for all route events
- confirm retries do not duplicate graph rows
- document the graph node/relationship mapping for route decisions

Verify:
- `pytest tests/test_event_outbox.py tests/test_event_dispatcher.py tests/test_route_events.py`
- `curl -s http://localhost:8088/admin/outbox | jq`

Success criteria:
- provenance can be replayed safely
- no prompt or response text is stored in route events

---

### P1.3 Close dashboard alignment gaps

Goal:
- Make dashboard summaries reflect the same canonical state used by routing.

Patch targets:
- `src/auto_router/ops_dashboard_routes.py`
- `src/auto_router/templates/fragments/dashboard_summary.html`
- `src/auto_router/templates/fragments/ops_summary.html`
- `src/auto_router/main.py`

What to change:
- show provider count and model count together
- show route signals and drift together
- show gateway status if enabled
- show local vs cloud split clearly

Verify:
- `pytest tests/test_ops_dashboard_routes.py`
- `curl -s http://localhost:8088/dashboard | head`
- `curl -s http://localhost:8088/admin/ops/summary | jq`

Success criteria:
- operators can read the same swarm state from the dashboard that the router uses internally

---

## P2 — Agentgateway and execution-plane follow-through

### P2.1 Finish agentgateway sidecar behavior

Goal:
- Make sidecar routing reliable enough to use as a normal path when enabled.

Patch targets:
- `src/auto_router/gateway.py`
- `src/auto_router/gateway_config.py`
- `src/auto_router/providers.py`
- `src/auto_router/main.py`
- `src/auto_router/main_live.py`
- `config/providers.example.yaml`
- `config/policies.example.yaml`
- `tests/test_agentgateway_config.py`
- `tests/test_agentgateway_provider.py`
- `tests/test_agentgateway_headers.py`
- `tests/test_agentgateway_privacy.py`
- `tests/test_agentgateway_fallback.py`

What to change:
- make header metadata complete and stable
- preserve streaming paths
- keep direct fallback behavior explicit and safe
- ensure private/local-only requests cannot spill to cloud during failover

Verify:
- `pytest tests/test_agentgateway_*.py`
- `make gateway-smoke`
- `make gateway-metrics`

Success criteria:
- sidecar traffic works for allowed routes
- fallback behavior is deterministic and safe

---

### P2.2 Add a router-side capability query for auto-assign

Goal:
- Let auto-assign ask the router for capabilities without learning provider transport details.

Patch targets:
- `src/auto_router/context.py`
- `src/auto_router/service_routes.py`
- `src/auto_router/assistx_tasks.py`
- `src/auto_router/main_live.py`

What to change:
- expose a clear read-only capability summary
- include provider, model, node, lane, privacy, health, and latency fields
- keep it stable enough for assignment scoring

Verify:
- `pytest tests/test_context_services.py tests/test_service_routes.py`
- `curl -s http://localhost:8088/admin/context | jq`

Success criteria:
- auto-assign can use router capability summaries without embedding provider knowledge

---

## 3. Patching sequence

Recommended order:

1. P0.1 canonical IDs
2. P0.2 live inventory as source of truth
3. P0.3 privacy hard stops
4. P1.1 route signal emission / hydration
5. P1.2 metadata-only AssistX write-back
6. P1.3 dashboard alignment
7. P2.1 agentgateway finalization
8. P2.2 router capability query for auto-assign

---

## 4. Command list

### Baseline validation
```bash
cd ~/git/auto-router
pytest tests/test_context_services.py tests/test_live_models.py tests/test_policy.py tests/test_route_events.py tests/test_ledger.py tests/test_ops_dashboard_routes.py
python -m py_compile src/auto_router/*.py
```

### Live smoke checks
```bash
curl -s http://localhost:8088/health | jq
curl -s http://localhost:8088/admin/context | jq
curl -s http://localhost:8088/admin/live-models | jq
curl -s http://localhost:8088/admin/ops/summary | jq
curl -s http://localhost:8088/v1/models | jq
```

### Agentgateway smoke checks
```bash
make gateway-up
make gateway-smoke
make gateway-metrics
```

### Outbox / provenance checks
```bash
curl -s http://localhost:8088/admin/outbox | jq
curl -s -X POST 'http://localhost:8088/admin/outbox/dispatch?dry_run=true&limit=10' | jq
```

### Dashboard checks
```bash
curl -s http://localhost:8088/dashboard | head -40
curl -s http://localhost:8088/api/dashboard/ops-summary | head -40
```

---

## 5. Done definition

This swarm-unification slice is done when:
- canonical provider/model/node IDs are stable everywhere
- live inventory, context projection, and dashboard counts all agree
- route decisions are emitted as metadata-only provenance
- privacy hard stops are enforced on every path
- agentgateway sidecar works when enabled
- auto-assign can consume capability summaries without knowing provider transport details

