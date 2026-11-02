from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

from auto_router import __version__
from auto_router.agent_jobs import AgentJobManager, build_agent_job_request, record_as_dict
from auto_router.logging_utils import install_logging_middleware, setup_logging
from auto_router.circuit_breaker import CircuitBreakerManager
from auto_router.config import (
    AgentWorkerRegistry,
    ContextSnapshot,
    PolicyRegistry,
    ProviderRegistry,
    load_agent_worker_registry,
    load_context_snapshot_async,
    load_policy_registry,
    load_provider_registry,
    _project_live_models,
)
from auto_router.ledger import RuntimeSample, UsageEvent, UsageLedger
from auto_router.usage_accounting import provider_reported_usage
from auto_router.model_registry import ModelRegistryStore
from auto_router.provider_circuit_diagnostics import annotate_health_with_open_model_circuits
from auto_router.signal_registry import ContextSignalStore, provider_health_signals, signal_snapshot
from auto_router.models import Priority, ProviderCandidate, ProviderHealth, ProviderResponse, RouterRequest
from auto_router.policy import PolicyEngine
from auto_router.providers import AgentGatewayProviderAdapter, ProviderError, ProviderStreamResponse, build_provider
from auto_router.gateway import build_agentgateway_status
from auto_router.live_model_routes import merge_discovered_lmstudio_providers, refresh_provider_models
from auto_router.memory_client import MemoryClient
from auto_router.memory_store import MemoryStore
from auto_router.ops_dashboard_routes import build_swarm_state_summary, _context_route_signal_summary, _fleet_dispatcher_stats, _fleet_loadout_report, _workflow_contract_summary
from auto_router.quota import build_quota_manager
from auto_router.route_events import enqueue_route_decision_event
from auto_router.security import require_admin
from auto_router.settings import get_settings
from auto_router.service_routes import build_outbox_dispatch_status, build_outbox_pressure_status, dispatch_outbox_cycle
from auto_router.ui_pages import get_ui_page_sections

templates = Jinja2Templates(directory="src/auto_router/templates")

_start_time = time.time()


class AppState:
    providers: ProviderRegistry
    policies: PolicyRegistry
    agents: AgentWorkerRegistry
    context: ContextSnapshot
    policy_engine: PolicyEngine
    quota: Any
    quota_backend: str
    ledger: UsageLedger
    model_registry: Any
    signal_registry: Any
    circuits: CircuitBreakerManager
    agent_jobs: AgentJobManager
    outbox_dispatch_lock: Any
    outbox_dispatch_status: dict[str, Any]


state = AppState()


async def load_state() -> None:
    settings = get_settings()
    state.providers = load_provider_registry(settings.provider_config)
    await merge_discovered_lmstudio_providers(state)
    state.policies = load_policy_registry(settings.policy_config)
    state.agents = load_agent_worker_registry(settings.agent_config)

    missing: list[str] = []
    for label, cfg in [("provider", settings.provider_config), ("policy", settings.policy_config), ("agent", settings.agent_config)]:
        if isinstance(cfg, str) and not Path(cfg).exists():
            missing.append(f"{label}: {cfg}")
    if missing:
        print(f"WARNING: Config files not found — {', '.join(missing)}")
    if not state.providers.enabled():
        print("WARNING: No providers enabled. All routing requests will return 503.")

    state.context = await load_context_snapshot_async(settings.context_config, state.providers, state.agents)
    state.ledger = UsageLedger(settings.database_url)
    state.model_registry = ModelRegistryStore(settings.database_url)
    state.signal_registry = ContextSignalStore(settings.database_url)
    state.memory_store = MemoryStore(settings.database_url)
    state.memory_client = MemoryClient(
        state.memory_store,
        base_url=settings.memory_service_url if settings.memory_enabled else None,
        timeout_seconds=settings.memory_timeout_seconds,
    )
    state.context = _project_live_models(state.context, state.providers, state.model_registry.latest_inventory())
    state.signal_registry.save_snapshot(state.context)
    state.context = state.signal_registry.hydrate_context(state.context)
    state.policy_engine = PolicyEngine(state.providers, state.policies, settings.default_profile, state.context)
    state.quota = build_quota_manager(settings.redis_url)
    state.quota_backend = state.quota.__class__.__name__
    state.circuits = CircuitBreakerManager()
    state.agent_jobs = AgentJobManager(
        state.agents.agent_workers,
        outcome_recorder=state.memory_client.record_agent_job_outcome,
    )
    state.outbox_dispatch_lock = asyncio.Lock()
    state.outbox_dispatch_status = {}
    # Initialize the AssistX event outbox up front so the background
    # outbox dispatcher (outbox_dispatch_task) can run from startup rather
    # than waiting for the first /api/routes/request or admin dispatch to
    # lazily create it. See LLD §3.5 W-52.
    from auto_router.route_events import ensure_event_outbox

    state.event_outbox = ensure_event_outbox(state)


async def refresh_context_task() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            settings = get_settings()
            source = str(settings.context_config)
            if source.startswith(("http://", "https://")):
                state.context = await load_context_snapshot_async(source, state.providers, state.agents)
                state.context = _project_live_models(state.context, state.providers, state.model_registry.latest_inventory())
                state.signal_registry.save_snapshot(state.context)
                state.context = state.signal_registry.hydrate_context(state.context)
                state.policy_engine.context = state.context
        except Exception as exc:  # pragma: no cover
            print(f"Error refreshing context: {exc}")


async def refresh_tailnet_provider_task() -> None:
    while True:
        await asyncio.sleep(120)
        try:
            settings = get_settings()
            changed = await merge_discovered_lmstudio_providers(state)
            if not changed:
                continue
            state.context = await load_context_snapshot_async(settings.context_config, state.providers, state.agents)
            state.context = _project_live_models(state.context, state.providers, state.model_registry.latest_inventory())
            await asyncio.to_thread(state.signal_registry.save_snapshot, state.context)
            state.context = state.signal_registry.hydrate_context(state.context)
            state.policy_engine.context = state.context
        except Exception as exc:  # pragma: no cover
            print(f"Error refreshing tailnet providers: {exc}")


async def refresh_live_models_task() -> None:
    """Continuously poll every LM Studio provider's /v1/models endpoint (every few
    seconds) and fold the live model list straight into the routing context. This
    is what keeps each node's advertised models current in real time -- e.g. when a
    node swaps its loaded model (3B -> a medium/large model) the new model is added
    to routing immediately instead of going stale for the cache TTL, and a model
    that gets unloaded is dropped just as fast. Runs forever ("never stops")."""
    settings = get_settings()
    interval = max(float(getattr(settings, "live_model_poll_interval_seconds", 5.0)), 1.0)
    while True:
        await asyncio.sleep(interval)
        try:
            await merge_discovered_lmstudio_providers(state)
            providers = [p for p in state.providers.enabled() if p.type == "lmstudio"]
            if not providers:
                continue
            await refresh_provider_models(state, providers)
        except Exception as exc:  # pragma: no cover
            print(f"Error polling live models: {exc}")


async def prune_task() -> None:
    """Periodically bound the sqlite history tables (registry snapshots/probes and
    usage/runtime ledger) so they cannot grow without limit. Unbounded growth makes
    every blocking sqlite write slow and starves the async event loop."""
    while True:
        await asyncio.sleep(60)
        try:
            if hasattr(state, "model_registry"):
                await asyncio.to_thread(state.model_registry.prune)
            if hasattr(state, "ledger"):
                await asyncio.to_thread(state.ledger.prune)
            if hasattr(state, "signal_registry"):
                await asyncio.to_thread(state.signal_registry.prune)
        except Exception as exc:  # pragma: no cover
            print(f"Error pruning registry/ledger: {exc}")


async def outbox_dispatch_task() -> None:
    """Background drain of the AssistX event outbox.

    Throughput is adaptive: when there is a backlog the dispatcher works harder
    (larger batches, shorter interval) so a large backlog self-clears in minutes
    instead of sitting at 'critical' pressure for hours; when the queue is empty
    it idles at the configured base interval. This keeps /health from reporting a
    spurious 'degraded' state just because telemetry is draining."""
    settings = get_settings()
    base_interval = max(float(settings.assistx_event_dispatch_interval_seconds), 1.0)
    batch_max = max(int(getattr(settings, "assistx_event_dispatch_batch_max", 200)), 1)
    while True:
        try:
            if not hasattr(state, "event_outbox"):
                await asyncio.sleep(base_interval)
                continue
            summary = state.event_outbox.summary()
            pending = int(summary.get("pending", 0))
            retry = int(summary.get("retry", 0))
            backlog = pending + retry
            if backlog <= 0:
                await asyncio.sleep(base_interval)
                continue
            # Scale effort with backlog: more pending -> bigger batch, shorter wait.
            batch = min(batch_max, max(25, backlog))
            # Back off toward base_interval as the queue empties.
            interval = base_interval if backlog < batch_max else 1.0
            result = await dispatch_outbox_cycle(state, limit=batch, dry_run=False, reason="scheduled")
            if backlog:
                print(
                    "AssistX outbox dispatch completed: "
                    f"configured={result.get('configured')} pending={pending} retry={retry} "
                    f"delivered={int(result.get('summary', {}).get('delivered', 0))}"
                )
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover
            print(f"Error dispatching AssistX outbox: {exc}")
            await asyncio.sleep(base_interval)


def _context_projection_summary() -> dict[str, Any]:
    context = getattr(state, "context", None)
    if context is None:
        return {"status": "missing", "degraded": True, "source": "", "revision": ""}
    return {
        "status": getattr(context, "projection_status", lambda: "bootstrap")(),
        "degraded": getattr(context, "is_projection_degraded", lambda: False)(),
        "error": getattr(context, "projection_error", lambda: "")(),
        "source": getattr(context, "source", ""),
        "revision": getattr(context, "revision", ""),
    }


async def _backlog_burn_loop() -> None:
    """Autonomously burn AssistX backlog so idle fleet compute is used.

    Calls the same burn-down path an operator would, on a timer. Gated by
    AUTO_ROUTER_ASSISTX_TASKS_URL being configured (otherwise no-op).
    """
    from auto_router.settings import get_settings as _settings

    await asyncio.sleep(15)
    interval = int(getattr(_settings(), "backlog_burn_interval_seconds", 60) or 60)
    logger.info("backlog-burn loop started (interval=%ss)", interval)
    while True:
        try:
            # Power brake: never burn backlog while any node is off DAILY power
            # (CONSERVE/SURVIVE) — batch jobs are the first thing an outage
            # sacrifices (FLEET-STANDARD-LOADOUTS.md rule 6).
            off_daily = {h: s["profile"] for h, s in _power_state_by_node().items() if s["profile"] != "daily"}
            if off_daily:
                logger.info("backlog-burn skipped: nodes off DAILY power: %s", off_daily)
                await asyncio.sleep(interval)
                continue
            url = f"http://127.0.0.1:{_settings().port}/admin/backlog/burn-down"
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    url,
                    json={"tasks": []},
                    params={"source": "assistx", "limit": 25, "queue": "backlog", "dispatch_limit": 25},
                    timeout=60,
                )
                if r.status_code == 200:
                    data = r.json()
                    sel = data.get("summary", {}).get("selected", 0)
                    if sel:
                        logger.info("backlog-burn selected %s task(s)", sel)
                else:
                    logger.warning("backlog-burn returned %s", r.status_code)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("backlog-burn loop error: %s", exc)
        await asyncio.sleep(interval)


async def lifespan(_: FastAPI):
    await load_state()
    from auto_router.otel import init_otel

    init_otel()
    refresh_task = asyncio.create_task(refresh_context_task())
    tailnet_refresh_task = asyncio.create_task(refresh_tailnet_provider_task())
    live_models_poll_task = asyncio.create_task(refresh_live_models_task())
    dispatch_task = asyncio.create_task(outbox_dispatch_task())
    prune_state_task = asyncio.create_task(prune_task())
    fleet_health_task = asyncio.create_task(refresh_fleet_health_task())
    persist_latency_t = asyncio.create_task(persist_latency_task())
    from auto_router.model_placement import RECONCILE_SECONDS, placement_reconcile_task

    placement_task = None
    if RECONCILE_SECONDS > 0:
        placement_task = asyncio.create_task(placement_reconcile_task())
    # Autonomous backlog-burn: periodically pull AssistX backlog candidates and
    # burn them down so idle fleet compute is consumed without manual triggers.
    # Reuses the same /admin/backlog/burn-down logic via self-POST.
    backlog_burn_task = asyncio.create_task(_backlog_burn_loop())
    try:
        yield
    finally:
        refresh_task.cancel()
        tailnet_refresh_task.cancel()
        live_models_poll_task.cancel()
        dispatch_task.cancel()
        prune_state_task.cancel()
        fleet_health_task.cancel()
        persist_latency_t.cancel()
        if placement_task is not None:
            placement_task.cancel()
        if backlog_burn_task is not None:
            backlog_burn_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass
        try:
            await live_models_poll_task
        except asyncio.CancelledError:
            pass
        try:
            await tailnet_refresh_task
        except asyncio.CancelledError:
            pass
        try:
            await dispatch_task
        except asyncio.CancelledError:
            pass
        try:
            await prune_state_task
        except asyncio.CancelledError:
            pass
        try:
            await fleet_health_task
        except asyncio.CancelledError:
            pass
        try:
            await persist_latency_t
        except asyncio.CancelledError:
            pass
        # Flush learned latency map to disk on shutdown so it survives the restart.
        try:
            if hasattr(state, "policy_engine"):
                await asyncio.to_thread(state.policy_engine.persist_latency, True)
        except Exception:
            pass


app = FastAPI(
    title="auto-router",
    version="0.1.0",
    description="Local-first OpenAI-compatible LLM router with free quota scheduling.",
    lifespan=lifespan,
)
setup_logging()
install_logging_middleware(app)

from fastapi.staticfiles import StaticFiles
from pathlib import Path
static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(exist_ok=True)
(static_dir / "vendor").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, Any]:
    open_circuits = [circuit for circuit in state.circuits.snapshot() if circuit["open"]]
    gateway_status = await build_agentgateway_status()
    context_projection = _context_projection_summary()
    model_registry_summary = state.model_registry.summary() if hasattr(state, "model_registry") else {}
    outbox_pressure = build_outbox_pressure_status(state)
    reserved_providers = []
    if hasattr(state, "policy_engine") and hasattr(state.policy_engine, "_provider_reserved"):
        reserved_providers = sorted({
            provider.name for provider in state.providers.enabled()
            if state.policy_engine._provider_reserved(provider)
        })
    blocked_providers = sorted(set(state.context.blocked_provider_names()) | set(reserved_providers))
    # A live telemetry trickle sits at 'warning' (draining, below the critical
    # threshold) during normal operation; only 'critical' backlog or dead-letters
    # should mark the whole system degraded. Open circuits still always degrade.
    overall_ok = not open_circuits and outbox_pressure["level"] != "critical"
    redis_ok = "not_configured"
    if hasattr(state, "quota") and hasattr(state.quota, "client"):
        try:
            redis_ok = "ok" if await asyncio.to_thread(state.quota.client.ping) else "down"
        except Exception:
            redis_ok = "down"
    
    return {
        "ok": overall_ok,
        "status": "ok" if overall_ok else "degraded",
        "service": "auto-router",
        "version": __version__,
        "uptime": time.time() - _start_time,
        "deps": {
            "redis": {"status": redis_ok},
        },
        "context_revision": state.context.revision,
        "context_source": state.context.source,
        "context_projection_status": context_projection["status"],
        "context_projection_degraded": context_projection["degraded"],
        "context_projection_error": context_projection["error"],
        "quota_backend": state.quota_backend,
        "providers_enabled": len(state.providers.enabled()),
        "local_providers": state.context.local_provider_names(),
        "free_api_providers": state.context.free_api_provider_names(),
        "blocked_providers": blocked_providers,
        "reserved_providers": reserved_providers,
        "model_registry_stale_providers": model_registry_summary.get("stale_providers", []) if hasattr(state, "model_registry") else [],
        "model_registry_error_providers": model_registry_summary.get("error_providers", []) if hasattr(state, "model_registry") else [],
        "running_local_nodes": state.context.running_local_node_names(),
        "agent_workers_configured": len(state.agents.agent_workers),
        "open_circuits": len(open_circuits),
        "time": int(time.time()),
        "gateway": gateway_status,
        "assistx_outbox_dispatch": build_outbox_dispatch_status(state),
        "assistx_outbox_pressure": outbox_pressure,
    }

@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    snapshots = state.quota.snapshots(state.providers.enabled())
    usage_summary = state.ledger.summary()
    circuits = state.circuits.snapshot()
    lines = ["# HELP auto_router_quota_remaining Remaining configured quota."]
    lines.append("# TYPE auto_router_quota_remaining gauge")
    for snapshot in snapshots:
        for dimension, values in snapshot.dimensions.items():
            remaining = values.get("remaining")
            if remaining is not None:
                lines.append(
                    'auto_router_quota_remaining{provider="%s",model="%s",dimension="%s"} %s'
                    % (snapshot.provider, snapshot.model, dimension, remaining)
                )
    lines.append("# HELP auto_router_requests_total Total routed request events.")
    lines.append("# TYPE auto_router_requests_total counter")
    lines.append(f"auto_router_requests_total {usage_summary.get('totals', {}).get('requests', 0)}")
    lines.append("# HELP auto_router_circuit_open Circuit breaker open state.")
    lines.append("# TYPE auto_router_circuit_open gauge")
    for circuit in circuits:
        lines.append(
            'auto_router_circuit_open{owner="%s"} %s'
            % (circuit["owner"], 1 if circuit["open"] else 0)
        )
    outbox_pressure = build_outbox_pressure_status(state)
    lines.append("# HELP auto_router_assistx_outbox_pressure_total Combined pending/retry/dead-letter outbox pressure.")
    lines.append("# TYPE auto_router_assistx_outbox_pressure_total gauge")
    lines.append(f"auto_router_assistx_outbox_pressure_total {int(outbox_pressure.get('pressure_total') or 0)}")
    lines.append("# HELP auto_router_assistx_outbox_pressure_active Whether outbox pressure is non-zero.")
    lines.append("# TYPE auto_router_assistx_outbox_pressure_active gauge")
    lines.append(f"auto_router_assistx_outbox_pressure_active {1 if outbox_pressure.get('active') else 0}")
    return "\n".join(lines) + "\n"


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> Any:
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"title": "auto-router dashboard", "ui_page_sections": get_ui_page_sections()},
    )


@app.get("/api/dashboard/summary", response_class=HTMLResponse)
async def dashboard_summary(request: Request) -> Any:
    settings = get_settings()
    provider_health_summary = state.model_registry.provider_health_reports() if hasattr(state, "model_registry") else []
    model_registry_summary = state.model_registry.summary() if hasattr(state, "model_registry") else {}
    fleet_dispatcher_stats = _fleet_dispatcher_stats()
    workflow_contract_summary = _workflow_contract_summary(fleet_dispatcher_stats)
    event_outbox = getattr(state, "event_outbox", None)
    outbox_summary = event_outbox.summary() if event_outbox is not None else {}
    outbox_dispatch_summary = build_outbox_dispatch_status(state)
    return templates.TemplateResponse(
        request=request,
        name="fragments/dashboard_summary.html",
        context={
            "snapshots": state.quota.snapshots(state.providers.enabled()),
            "provider_probe_summary": state.model_registry.probe_summary() if hasattr(state, "model_registry") else {},
            "provider_health_summary": provider_health_summary,
            "model_registry_summary": model_registry_summary,
            "agents": state.agents.agent_workers,
            "jobs": list(state.agent_jobs.jobs.values()),
            "recent_usage": state.ledger.recent_events(limit=20),
            "runtime_summary": state.ledger.runtime_summary() if hasattr(state, "ledger") and hasattr(state.ledger, "runtime_summary") else {},
            "recent_runtime_samples": state.ledger.recent_runtime_samples(limit=12) if hasattr(state, "ledger") and hasattr(state.ledger, "recent_runtime_samples") else [],
            "context": state.context,
            "context_projection": _context_projection_summary(),
            "context_graph_summary": state.context.graph_object_summary() if hasattr(state.context, "graph_object_summary") else {},
            "context_route_signal_summary": _context_route_signal_summary(state),
            "circuits": state.circuits.snapshot(),
            "gateway": await build_agentgateway_status(),
            "ui_page_sections": get_ui_page_sections(),
            "assistx_tasks_url": None,
            "assistx_event_sink_url": None,
            "assistx_tasks_configured": bool(settings.assistx_tasks_url),
            "assistx_event_sink_configured": bool(settings.assistx_event_sink_url),
            "outbox_summary": outbox_summary,
            "outbox_dispatch_summary": outbox_dispatch_summary,
            "outbox_pressure_summary": build_outbox_pressure_status(state),
            "fleet_dispatcher_stats": fleet_dispatcher_stats,
            "fleet_loadout_report": _fleet_loadout_report(),
            "workflow_contract_summary": workflow_contract_summary,
            **build_swarm_state_summary(state, provider_health_summary=provider_health_summary),
        },
    )


@app.get("/admin/quota")
async def admin_quota(_: None = Depends(require_admin)) -> dict[str, Any]:
    return {"providers": [snapshot.model_dump() for snapshot in state.quota.snapshots(state.providers.enabled())]}


@app.get("/admin/providers/health")
async def admin_provider_health(_: None = Depends(require_admin)) -> dict[str, Any]:
    return {"providers": await _provider_health_reports()}


@app.get("/admin/context")
async def admin_context(_: None = Depends(require_admin)) -> dict[str, Any]:
    return state.context.model_dump()


@app.get("/admin/context/graph")
async def admin_context_graph(_: None = Depends(require_admin)) -> dict[str, Any]:
    return {
        "revision": state.context.revision,
        "source": state.context.source,
        "summary": state.context.graph_object_summary(),
        "objects": state.context.graph_objects(),
    }


@app.get("/admin/providers")
async def admin_providers(_: None = Depends(require_admin)) -> dict[str, Any]:
    return {"providers": [provider.model_dump() for provider in state.providers.providers]}


@app.get("/admin/placement")
async def admin_placement(_: None = Depends(require_admin)) -> dict[str, Any]:
    from auto_router.model_placement import compute_plan

    return compute_plan(state)


@app.post("/admin/load")
async def admin_load(apply: bool | None = None, _: None = Depends(require_admin)) -> dict[str, Any]:
    from auto_router.model_placement import AUTOLOAD_ENABLED, reconcile_once

    do_apply = apply if apply is not None else AUTOLOAD_ENABLED
    return await reconcile_once(state, apply=do_apply)


@app.get("/admin/agent-workers")
async def admin_agent_workers(_: None = Depends(require_admin)) -> dict[str, Any]:
    return {"agent_workers": [worker.model_dump() for worker in state.agents.agent_workers]}


@app.get("/admin/usage")
async def admin_usage(limit: int = 50, _: None = Depends(require_admin)) -> dict[str, Any]:
    return {"summary": state.ledger.summary(), "recent": state.ledger.recent_events(limit=limit)}


@app.get("/admin/circuits")
async def admin_circuits(_: None = Depends(require_admin)) -> dict[str, Any]:
    return {"circuits": state.circuits.snapshot()}


@app.post("/admin/circuits/reset")
async def admin_circuits_reset(_: None = Depends(require_admin)) -> dict[str, Any]:
    """Clear all circuit breakers (recover from tripped providers immediately)."""
    cleared = state.circuits.reset()
    return {"cleared": cleared}


@app.post("/admin/circuits/reset/{owner:path}")
async def admin_circuits_reset_owner(owner: str, _: None = Depends(require_admin)) -> dict[str, Any]:
    """Clear a single circuit breaker by owner (provider/model)."""
    cleared = state.circuits.reset(owner)
    return {"owner": owner, "cleared": cleared}


@app.get("/admin/agent-jobs")
async def admin_agent_jobs(_: None = Depends(require_admin)) -> dict[str, Any]:
    return {"jobs": [record_as_dict(record) for record in state.agent_jobs.list_records()]}


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    data = []
    for provider in state.providers.enabled():
        canonical_provider = state.context.canonical_provider_name(provider.name)
        context_provider = state.context.provider_for(canonical_provider)
        lane = _lane_for_provider(provider, context_provider)
        for model in provider.models:
            canonical_model = state.context.canonical_model_id(f"{provider.name}.{model.provider_model}")
            raw_ctx = getattr(model, "context_length", None) or getattr(model, "context_window", None)
            if isinstance(raw_ctx, dict):
                raw_ctx = raw_ctx.get("value") or raw_ctx.get("context_length")
            ctx = int(raw_ctx) if isinstance(raw_ctx, (int, float)) and raw_ctx > 0 else 128000
            data.append(
                {
                    "id": canonical_model,
                    "object": "model",
                    "created": 0,
                    "owned_by": canonical_provider,
                    "provider_model": model.provider_model,
                    "capabilities": sorted(model.capabilities),
                    "lane": lane,
                    "local": lane == "local",
                    "free_api": lane == "free_api",
                    "blocked": bool(context_provider.is_blocked) if context_provider is not None else False,
                    "context_length": ctx,
                    "context_window": ctx,
                }
            )
    data.extend(
        [
            {"id": "auto/fast", "object": "model", "created": 0, "owned_by": "auto-router", "context_length": 128000, "context_window": 128000},
            {"id": "auto/flash-start", "object": "model", "created": 0, "owned_by": "auto-router", "context_length": 128000, "context_window": 128000},
            {"id": "auto/high-quality", "object": "model", "created": 0, "owned_by": "auto-router", "context_length": 128000, "context_window": 128000},
            {"id": "auto/code", "object": "model", "created": 0, "owned_by": "auto-router", "context_length": 128000, "context_window": 128000},
            {"id": "auto/review", "object": "model", "created": 0, "owned_by": "auto-router", "context_length": 128000, "context_window": 128000},
            {"id": "auto/iterate", "object": "model", "created": 0, "owned_by": "auto-router", "context_length": 128000, "context_window": 128000},
            {"id": "auto/finalize", "object": "model", "created": 0, "owned_by": "auto-router", "context_length": 128000, "context_window": 128000},
            {"id": "auto/local", "object": "model", "created": 0, "owned_by": "auto-router", "context_length": 128000, "context_window": 128000},
            {"id": "auto/sophia", "object": "model", "created": 0, "owned_by": "auto-router", "context_length": 128000, "context_window": 128000},
            {"id": "auto/backlog-burn", "object": "model", "created": 0, "owned_by": "auto-router", "context_length": 128000, "context_window": 128000},
        ]
    )
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    return await _execute(_router_request("chat_completions", await request.json()))


@app.post("/v1/responses", response_model=None)
async def responses(request: Request) -> JSONResponse | StreamingResponse:
    return await _execute(_router_request("responses", await request.json()))


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> JSONResponse:
    router_request = _router_request("embeddings", await request.json())
    router_request.required_capabilities.add("embeddings")
    return await _execute(router_request)


@app.post("/v1/completions", response_model=None)
async def completions(request: Request) -> JSONResponse | StreamingResponse:
    return await _execute(_router_request("completions", await request.json()))


@app.post("/jobs/agent")
async def create_agent_job(request: Request, _: None = Depends(require_admin)) -> dict[str, Any]:
    job_request = build_agent_job_request(await request.json())
    record = state.agent_jobs.submit(job_request)
    return {
        "job_id": record.request.job_id,
        "status": record.status,
        "worker_name": record.worker_name,
        "request": job_request.model_dump(),
    }


@app.get("/jobs/agent/{job_id}")
async def get_agent_job(job_id: str) -> dict[str, Any]:
    record = state.agent_jobs.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "job not found"})
    return record_as_dict(record)


@app.get("/jobs/agent/{job_id}/artifacts")
async def get_agent_job_artifacts(job_id: str) -> dict[str, Any]:
    record = state.agent_jobs.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "job not found"})
    return {"job_id": job_id, "artifacts": state.agent_jobs.artifacts_for(job_id)}


def _apply_power_tiers_to_policy() -> None:
    """Stamp power-tier reports onto the policy engine's provider_health map
    under the names routing actually uses (state.providers composite names).
    Runs per-request alongside the fleet-health-cache assignment; cheap dict
    work, and only mutates entries for nodes whose tier differs from DAILY."""
    power_map = _power_profile_by_node()
    if not power_map:
        return
    try:
        current = state.policy_engine.provider_health
    except Exception:
        return
    if not isinstance(current, dict):
        current = {}
    node_map = _providers_by_node()
    stamped = False
    for host_lower, profile in power_map.items():
        for provider_name in node_map.get(host_lower, []):
            entry = current.get(provider_name)
            if not isinstance(entry, dict):
                entry = {
                    "provider": provider_name,
                    "ok": True,
                    "stale": False,
                    "age_seconds": 0,
                    "model_count": None,
                    "health_score": 100,
                }
                current[provider_name] = entry
            prev_profile = entry.get("power_profile")
            model_class = _power_state_by_node().get(host_lower, {}).get("model_class", "full")
            _power_tier_apply(entry, profile, model_class)
            if profile == "daily":
                # keep probe-derived scoring authoritative; just mark tier
                entry.pop("error", None) if entry.get("error", "").startswith("power-") else None
            if prev_profile != profile:
                if profile == "daily":
                    logger.info("routing restored to DAILY: %s", provider_name)
                else:
                    logger.info("routing gated by power tier: %s -> %s", provider_name, profile)
            stamped = True
    if stamped:
        state.policy_engine.provider_health = current


async def _execute(router_request: RouterRequest) -> JSONResponse | StreamingResponse:
    # Feed the router's probe-history health into the policy engine so routing
    # can demote/exclude flaky + unloaded nodes (liveness-gated routing).
    state.policy_engine.provider_health = _fleet_health_map()
    # Overlay fleet power tiers (DAILY/CONSERVE/SURVIVE) so battery-state nodes
    # are demoted/parked on top of probe evidence.
    _apply_power_tiers_to_policy()
    plan = state.policy_engine.plan(router_request)
    settings = get_settings()
    # Hard bounds so a fleet full of dead/hung nodes can never make a single
    # request hang for (attempt_timeout * candidate_count). We stop trying once
    # the deadline passes or we've burned max_candidate_attempts.
    deadline = time.monotonic() + settings.request_deadline_seconds
    attempts = 0
    timed_out = False
    errors: list[str] = []
    for stage in plan.stages:
        if not stage.candidates and stage.optional:
            continue
        stage_rejections: list[str] = []
        # Release the plan-time reservation for this stage's top candidate now that
        # we're committing to executing it (in-flight tracking takes over from here).
        if stage.candidates:
            state.policy_engine.mark_planned_end(
                f"{stage.candidates[0].provider.name}/{stage.candidates[0].model.alias}"
            )
        for candidate in stage.candidates:
            if time.monotonic() > deadline or attempts >= settings.max_candidate_attempts:
                timed_out = True
                break
            attempts += 1
            owner = _owner(candidate)
            if not _candidate_allowed(router_request, candidate, state.context):
                rejection = f"not allowed for {owner}"
                stage_rejections.append(rejection)
                continue
            if not state.circuits.allowed(owner):
                rejection = f"circuit open for {owner}"
                stage_rejections.append(rejection)
                errors.append(rejection)
                continue
            estimate = await asyncio.to_thread(state.quota.estimate, candidate.model, router_request.raw_body)
            if not await asyncio.to_thread(state.quota.reserve, candidate.provider, candidate.model, estimate):
                rejection = f"quota unavailable for {candidate.provider.name}/{candidate.model.alias}"
                stage_rejections.append(rejection)
                errors.append(rejection)
                continue
            await asyncio.to_thread(
                enqueue_route_decision_event,
                state,
                request=router_request,
                profile_name=plan.profile_name,
                stage=stage.purpose.value,
                chosen_candidate=candidate,
                candidates=stage.candidates,
                rejections=stage_rejections,
            )
            state.policy_engine.mark_inflight_start(owner)
            provider = build_provider(
                candidate.provider,
                timeout_seconds=get_settings().attempt_timeout_seconds,
                connect_timeout_seconds=get_settings().connect_timeout_seconds,
            )
            started_at_ms = int(time.time() * 1000)
            started = time.perf_counter()
            try:
                if router_request.stream and router_request.route in {"chat_completions", "responses", "completions"}:
                    gateway_context = _gateway_route_context(plan.profile_name, stage.purpose.value, router_request)
                    stream_response = await asyncio.wait_for(
                        _dispatch_stream(provider, candidate, router_request, route_plan=gateway_context),
                        timeout=settings.attempt_timeout_seconds,
                    )
                    state.policy_engine.mark_inflight_end(owner)
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    ended_at_ms = int(time.time() * 1000)
                    state.policy_engine.mark_latency(owner, latency_ms)
                    state.circuits.record_success(owner)
                    gateway_metadata = None
                    if stream_response.provider.startswith("agentgateway"):
                        gateway_metadata = {
                            "provider": stream_response.provider,
                            "profile": gateway_context.profile,
                            "stage": gateway_context.stage,
                            "privacy": gateway_context.privacy,
                            "quota_mode": gateway_context.quota_mode,
                            "latency_ms": latency_ms,
                        }
                    await asyncio.to_thread(
                        _record_usage,
                        router_request,
                        stream_response.provider,
                        stream_response.model,
                        stage.purpose.value,
                        estimate,
                        stream_response.status_code,
                        latency_ms,
                        gateway_metadata=gateway_metadata,
                        started_at_ms=started_at_ms,
                        ended_at_ms=ended_at_ms,
                    )
                    return StreamingResponse(
                        stream_response.body,
                        status_code=stream_response.status_code,
                        media_type=stream_response.headers.get("content-type", "text/event-stream"),
                        headers={
                            "x-auto-router-provider": stream_response.provider,
                            "x-auto-router-model": stream_response.model,
                            "x-auto-router-stage": stage.purpose.value,
                            "x-auto-router-profile": plan.profile_name,
                        },
                    )
                gateway_context = _gateway_route_context(plan.profile_name, stage.purpose.value, router_request)
                response = await asyncio.wait_for(
                    _dispatch(provider, candidate, router_request, route_plan=gateway_context),
                    timeout=settings.attempt_timeout_seconds,
                )
                state.policy_engine.mark_inflight_end(owner)
                latency_ms = int((time.perf_counter() - started) * 1000)
                state.policy_engine.mark_latency(owner, latency_ms)
                state.circuits.record_success(owner)
                gateway_metadata = response.data.get("_gateway_metadata") if isinstance(response.data, dict) else None
                ended_at_ms = int(time.time() * 1000)
                await asyncio.to_thread(
                    _record_usage,
                    router_request,
                    response.provider,
                    response.model,
                    stage.purpose.value,
                    estimate,
                    response.status_code,
                    latency_ms,
                    response.usage,
                    gateway_metadata=gateway_metadata,
                    started_at_ms=started_at_ms,
                    ended_at_ms=ended_at_ms,
                )
                payload = _normalize_response_payload(response, stage.purpose.value, plan.profile_name)
                payload["auto_router"]["latency_ms"] = latency_ms
                return JSONResponse(payload, status_code=response.status_code)
            except ProviderError as exc:
                state.policy_engine.mark_inflight_end(owner)
                latency_ms = int((time.perf_counter() - started) * 1000)
                await asyncio.to_thread(state.quota.release, candidate.provider, candidate.model, estimate)
                state.circuits.record_failure(owner, str(exc), retry_after=_retry_after_seconds(exc))
                ended_at_ms = int(time.time() * 1000)
                await asyncio.to_thread(
                    _record_usage,
                    router_request,
                    candidate.provider.name,
                    candidate.model.provider_model,
                    stage.purpose.value,
                    estimate,
                    exc.status_code,
                    latency_ms,
                    error=exc,
                    started_at_ms=started_at_ms,
                    ended_at_ms=ended_at_ms,
                )
                stage_rejections.append(str(exc))
                errors.append(str(exc))
            except Exception as exc:
                state.policy_engine.mark_inflight_end(owner)
                latency_ms = int((time.perf_counter() - started) * 1000)
                await asyncio.to_thread(state.quota.release, candidate.provider, candidate.model, estimate)
                state.circuits.record_failure(owner, str(exc))
                ended_at_ms = int(time.time() * 1000)
                await asyncio.to_thread(
                    _record_usage,
                    router_request,
                    candidate.provider.name,
                    candidate.model.provider_model,
                    stage.purpose.value,
                    estimate,
                    None,
                    latency_ms,
                    error=exc,
                    started_at_ms=started_at_ms,
                    ended_at_ms=ended_at_ms,
                )
                stage_rejections.append(f"unexpected provider error: {exc}")
                errors.append(f"unexpected provider error: {exc}")
        if timed_out:
            break
    if timed_out:
        raise HTTPException(
            status_code=504,
            detail={"error": "request deadline exceeded; too many dead/hung nodes", "details": errors},
        )
    raise HTTPException(status_code=503, detail={"error": "all providers failed", "details": errors})


def _gateway_route_context(profile_name: str, stage: str, request: RouterRequest) -> SimpleNamespace:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    privacy = metadata.get("privacy")
    if request.local_only or request.priority == Priority.local_only or request.allow_cloud is False:
        privacy = "local_only"
    elif not isinstance(privacy, str) or not privacy.strip():
        privacy = "cloud_allowed"
    quota_mode = metadata.get("quota_mode")
    if not isinstance(quota_mode, str) or not quota_mode.strip():
        quota_mode = "balanced"
    return SimpleNamespace(
        profile=profile_name,
        stage=stage,
        privacy=privacy,
        quota_mode=quota_mode,
        priority=request.priority.value,
        task_id=request.task_id or metadata.get("task_id"),
        agent_run_id=request.agent_run_id or metadata.get("agent_run_id"),
        node_id=request.node_id or metadata.get("node_id"),
        context_revision=metadata.get("context_revision"),
    )


async def _dispatch(provider: Any, candidate: ProviderCandidate, request: RouterRequest, route_plan: Any | None = None) -> ProviderResponse:
    provider_model = candidate.model.provider_model
    if request.route == "chat_completions":
        if isinstance(provider, AgentGatewayProviderAdapter):
            return await provider.chat_completions(request, provider_model, route_plan=route_plan)
        return await provider.chat_completions(request, provider_model)
    if request.route == "responses":
        if isinstance(provider, AgentGatewayProviderAdapter):
            return await provider.responses(request, provider_model)
        return await provider.responses(request, provider_model)
    if request.route == "embeddings":
        return await provider.embeddings(request, provider_model)
    if request.route == "completions":
        if isinstance(provider, AgentGatewayProviderAdapter):
            return await provider.completions(request, provider_model)
        return await provider.completions(request, provider_model)
    raise ProviderError(f"unsupported route {request.route}", retryable=False)


async def _dispatch_stream(provider: Any, candidate: ProviderCandidate, request: RouterRequest, route_plan: Any | None = None) -> ProviderStreamResponse:
    provider_model = candidate.model.provider_model
    if request.route == "chat_completions":
        if isinstance(provider, AgentGatewayProviderAdapter):
            return await provider.stream_chat_completions(request, provider_model, route_plan=route_plan)
        return await provider.stream_chat_completions(request, provider_model)
    if request.route == "responses":
        if isinstance(provider, AgentGatewayProviderAdapter):
            return await provider.stream_responses(request, provider_model)
        return await provider.stream_responses(request, provider_model)
    if request.route == "completions":
        if isinstance(provider, AgentGatewayProviderAdapter):
            return await provider.stream_completions(request, provider_model)
        return await provider.stream_completions(request, provider_model)
    raise ProviderError(f"unsupported stream route {request.route}", retryable=False)


def _request_privacy_forces_local(metadata: dict[str, Any], model: str | None = None) -> bool:
    model_text = model or ""
    if model_text.startswith("auto/private") or model_text.startswith("auto/local") or model_text.startswith("auto/sophia"):
