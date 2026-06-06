# Auto-router Swarm Provider Operations Plan

> **For Hermes:** Use subagent-driven-development to implement this plan task-by-task after approval.

**Goal:** Turn provider/model discovery into a first-class swarm operation: probe every provider, detect drift, score health, trace routing decisions, and surface it all in the dashboard and metrics.

**Architecture:**
Auto-router already projects live `/models` inventory into `ContextSnapshot` and persists durable snapshots in `ModelRegistryStore`. The next step is to make provider tap behavior operationally visible and actionable. We will add per-provider probing, persist probe history, compute a health score from recent probes, emit routing decision traces, and expose these signals in the dashboard and ops metrics. The implementation should keep live model projection as the single source of truth for inventory while layering observability and decision support on top.

**Tech Stack:** Python/FastAPI, Jinja2 templates, pytest, Prometheus metrics, existing auto-router context/provider/model registry modules.

---

## Current State

Already in place:
- Live model projection into `ContextSnapshot.models`
- Durable `ModelRegistryStore` for `/models` snapshots
- `/admin/providers/probe` endpoint for probing provider `/models`
- Ops dashboard summary and metrics for provider tap counts

What is still missing:
- Per-provider probe detail and history
- Drift detection when a provider’s `/models` response changes shape or content unexpectedly
- Provider health score that is derived from probe history
- Route decision trace records for debugging and explainability
- UI panels that show provider health, drift, and recent probe outcomes per provider
- Scheduled probe/alert behavior for recurring validation

---

## Proposed Implementation Plan

### Task 1: Add a durable provider probe record

**Objective:** Store each provider probe result in a structured way so history can be summarized and queried later.

**Files likely to change:**
- Modify: `src/auto_router/model_registry.py`
- Modify: `src/auto_router/live_model_routes.py`
- Test: `tests/test_model_registry.py`
- Test: `tests/test_live_models.py`

**Plan:**
- Extend the durable registry to persist probe metadata alongside live model snapshots.
- Record at least:
  - provider name
  - timestamp
  - success/failure
  - latency_ms
  - model count
  - error summary
  - response signature or hash for drift comparison
- Keep the structure small and append-only so it remains cheap to query.

**Validation:**
- A new test persists a probe record and retrieves it from the durable store.
- Existing snapshot tests continue to pass.

---

### Task 2: Implement per-provider probe and drift detection

**Objective:** Probe one provider at a time, compare the latest response to prior snapshots, and mark drift when the live model set changes materially.

**Files likely to change:**
- Modify: `src/auto_router/live_model_routes.py`
- Modify: `src/auto_router/live_models.py`
- Modify: `src/auto_router/ops_dashboard_routes.py`
- Test: `tests/test_live_models.py`

**Plan:**
- Add a helper that probes a single provider endpoint and returns a normalized result object.
- Compare the current normalized model list against the previous successful probe for that provider.
- Detect drift when:
  - model count changes unexpectedly
  - model IDs disappear
  - provider returns a shape that can’t be normalized cleanly
- Capture a compact drift summary so the dashboard can show “changed / unchanged / broken”.

**Validation:**
- A test confirms a changed model list produces a drift flag.
- A failure response is recorded as a failed probe with a useful error summary.

---

### Task 3: Compute provider health scores from probe history

**Objective:** Turn probe history into a simple health score the router and dashboard can use.

**Files likely to change:**
- Modify: `src/auto_router/model_registry.py`
- Modify: `src/auto_router/ops_dashboard_routes.py`
- Modify: `src/auto_router/providers.py` if provider selection needs to consume the score
- Test: `tests/test_ops_dashboard_routes.py`
- Test: `tests/test_live_models.py`

**Plan:**
- Define a deterministic score using recent probe outcomes, latency, and model availability.
- Keep the score interpretable, not ML-driven.
- Expose the score in:
  - ops summary JSON
  - Prometheus metrics
  - dashboard UI
- Use the score as an input signal, not the only signal, for routing choices.

**Suggested scoring inputs:**
- recent success rate
- last successful probe age
- average latency
- recent error streak
- current live model count

**Validation:**
- A test with synthetic probe history yields the expected score ordering.

---

### Task 4: Add route decision tracing

**Objective:** Explain why the router chose a given provider/model for a request.

**Files likely to change:**
- Modify: `src/auto_router/route_events.py`
- Modify: `src/auto_router/route_event_patch.py`
- Modify: `src/auto_router/main.py` or the route selection path that emits decision events
- Modify: `src/auto_router/ops_dashboard_routes.py`
- Test: `tests/test_route_events.py`

**Plan:**
- Record a compact decision trace for each routed request:
  - request intent / policy alias
  - candidate providers/models
  - chosen provider/model
  - reason for selection
  - fallback or rejection reasons
- Keep the trace small enough to store alongside other operational events.
- Surface the latest traces in ops summary or a dedicated endpoint.

**Validation:**
- Tests assert the trace includes the chosen provider/model and the reason.
- A rejection/fallback case is represented clearly.

---

### Task 5: Expose provider health and drift in the dashboard

**Objective:** Make the state of the provider swarm visible at a glance.

**Files likely to change:**
- Modify: `src/auto_router/templates/dashboard.html`
- Modify: `src/auto_router/templates/fragments/dashboard_summary.html`
- Modify: `src/auto_router/templates/fragments/ops_summary.html`
- Modify: `src/auto_router/ops_dashboard_routes.py`
- Test: `tests/test_ops_dashboard_routes.py`

**Plan:**
- Add a provider health panel showing:
  - provider name
  - health score
  - last probe status
  - model count
  - last probe age
  - drift status
- Add a recent probe history view or compact table.
- Keep the UI terse and operator-friendly.
- Add a small visual distinction for healthy / degraded / broken providers.

**Validation:**
- HTML tests or route tests confirm the new fields appear in rendered output.
- Summary JSON contains the provider health entries.

---

### Task 6: Add metrics for probes, drift, and routing explainability

**Objective:** Make provider health and routing state visible to monitoring tools.

**Files likely to change:**
- Modify: `src/auto_router/ops_dashboard_routes.py`
- Test: `tests/test_ops_dashboard_routes.py`

**Plan:**
- Emit Prometheus metrics for:
  - probe success/failure counts
  - probe latency
  - provider health score
  - drift count
  - route decision count by provider/model
- Keep metric names stable and low-cardinality.

**Validation:**
- Tests check the metrics text includes the new series.

---

### Task 7: Add scheduled probes and alert hooks

**Objective:** Make provider validation happen automatically instead of only on demand.

**Files likely to change:**
- Modify: `src/auto_router/main.py`
- Modify: `src/auto_router/live_model_routes.py`
- Optional: `src/auto_router/event_outbox.py`
- Optional: cron or scheduler wiring already present in the app
- Test: `tests/test_live_models.py`

**Plan:**
- Add a scheduled probe job that probes providers at a configurable interval.
- Emit an alert/event when:
  - a provider starts failing
  - a provider’s model count drops to zero
  - drift occurs repeatedly
- Keep alerts rate-limited so noisy providers don’t flood the system.

**Validation:**
- A test simulates repeated probe failures and verifies only the expected alert is emitted.

---

## Suggested Execution Order

1. Durable probe record
2. Per-provider probe + drift detection
3. Provider health score
4. Route decision tracing
5. Dashboard UI for health/drift/traces
6. Metrics expansion
7. Scheduled probes and alerts

This order keeps the work incremental and testable. Each step should leave the system in a usable state.

---

## Risks / Tradeoffs

- Probe history can grow quickly if stored too verbosely; keep records compact.
- Health scoring can become opaque if over-engineered; keep it simple and explainable.
- Routing traces can leak sensitive request details; redact aggressively and store only what is needed to explain selection.
- Metrics cardinality can explode if provider/model labels are too granular; use stable canonical IDs.
- Drift detection should tolerate harmless ordering changes but still catch meaningful inventory changes.

---

## Open Questions Before Implementation

1. Should health scores be purely derived from probe history, or should routing failures also influence them?
2. Do we want drift detection on model count only, or on normalized model identity sets as well?
3. Should route decision traces be stored in SQLite, the existing event outbox, or both?
4. Do we want scheduled probes to cover all providers equally, or prioritize providers used by active policies?

---

## Verification Checklist

Before calling the work complete:
- `pytest -q` or the relevant targeted subset passes
- `/admin/ops/summary` includes provider health and drift info
- `/metrics/ops` exposes probe, drift, and health metrics
- Dashboard shows provider health at a glance
- Provider tap probes still update `ContextSnapshot.models`
- Redaction is verified for any trace/event payloads

---

## Next Step After Approval

Implement this plan in small TDD increments, starting with durable probe records and per-provider drift detection.
