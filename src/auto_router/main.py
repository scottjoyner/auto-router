from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

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
from auto_router.model_registry import ModelRegistryStore
from auto_router.signal_registry import ContextSignalStore, provider_health_signals, signal_snapshot
from auto_router.models import Priority, ProviderCandidate, ProviderHealth, ProviderResponse, RouterRequest
from auto_router.policy import PolicyEngine
from auto_router.providers import AgentGatewayProviderAdapter, ProviderError, ProviderStreamResponse, build_provider
from auto_router.gateway import build_agentgateway_status
from auto_router.ops_dashboard_routes import build_swarm_state_summary, _context_route_signal_summary
from auto_router.quota import build_quota_manager
from auto_router.route_event_patch import install_route_event_patch
from auto_router.route_events import enqueue_route_decision_event
from auto_router.settings import get_settings
from auto_router.service_routes import build_outbox_dispatch_status, dispatch_outbox_cycle

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
    state.context = _project_live_models(state.context, state.providers, state.model_registry.latest_inventory())
    state.signal_registry.save_snapshot(state.context)
    state.context = state.signal_registry.hydrate_context(state.context)
    state.policy_engine = PolicyEngine(state.providers, state.policies, settings.default_profile, state.context)
    state.quota = build_quota_manager(settings.redis_url)
    state.quota_backend = state.quota.__class__.__name__
    state.circuits = CircuitBreakerManager()
    state.agent_jobs = AgentJobManager(state.agents.agent_workers)
    state.outbox_dispatch_lock = asyncio.Lock()
    state.outbox_dispatch_status = {}


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


async def outbox_dispatch_task() -> None:
    settings = get_settings()
    interval = max(float(settings.assistx_event_dispatch_interval_seconds), 1.0)
    while True:
        await asyncio.sleep(interval)
        try:
            if not hasattr(state, "event_outbox"):
                continue
            result = await dispatch_outbox_cycle(state, limit=25, dry_run=False, reason="scheduled")
            pending = int(result.get("summary", {}).get("pending", 0))
            retry = int(result.get("summary", {}).get("retry", 0))
            if pending or retry:
                print(
                    "AssistX outbox dispatch completed: "
                    f"configured={result.get('configured')} pending={pending} retry={retry} "
                    f"delivered={int(result.get('summary', {}).get('delivered', 0))}"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover
            print(f"Error dispatching AssistX outbox: {exc}")


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    await load_state()
    refresh_task = asyncio.create_task(refresh_context_task())
    dispatch_task = asyncio.create_task(outbox_dispatch_task())
    try:
        yield
    finally:
        refresh_task.cancel()
        dispatch_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass
        try:
            await dispatch_task
        except asyncio.CancelledError:
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
async def health() -> dict[str, Any]:
    open_circuits = [circuit for circuit in state.circuits.snapshot() if circuit["open"]]
    gateway_status = await build_agentgateway_status()
    context_projection = _context_projection_summary()
    overall_ok = not open_circuits
    redis_ok = "not_configured"
    if hasattr(state, "quota") and hasattr(state.quota, "client"):
        try:
            redis_ok = "ok" if state.quota.client.ping() else "down"
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
        "blocked_providers": state.context.blocked_provider_names(),
        "running_local_nodes": state.context.running_local_node_names(),
        "agent_workers_configured": len(state.agents.agent_workers),
        "open_circuits": len(open_circuits),
        "time": int(time.time()),
        "gateway": gateway_status,
        "assistx_outbox_dispatch": build_outbox_dispatch_status(state),
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
    return "\n".join(lines) + "\n"


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> Any:
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"title": "auto-router dashboard"},
    )


@app.get("/api/dashboard/summary", response_class=HTMLResponse)
async def dashboard_summary(request: Request) -> Any:
    return templates.TemplateResponse(
        request=request,
        name="fragments/dashboard_summary.html",
        context={
            "snapshots": state.quota.snapshots(state.providers.enabled()),
            "provider_health": await _provider_health_reports(),
            "provider_probe_summary": state.model_registry.probe_summary() if hasattr(state, "model_registry") else {},
            "provider_health_summary": state.model_registry.provider_health_reports() if hasattr(state, "model_registry") else [],
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
            **build_swarm_state_summary(state),
        },
    )


@app.get("/admin/quota")
async def admin_quota() -> dict[str, Any]:
    return {"providers": [snapshot.model_dump() for snapshot in state.quota.snapshots(state.providers.enabled())]}


@app.get("/admin/providers/health")
async def admin_provider_health() -> dict[str, Any]:
    return {"providers": await _provider_health_reports()}


@app.get("/admin/context")
async def admin_context() -> dict[str, Any]:
    return state.context.model_dump()


@app.get("/admin/context/graph")
async def admin_context_graph() -> dict[str, Any]:
    return {
        "revision": state.context.revision,
        "source": state.context.source,
        "summary": state.context.graph_object_summary(),
        "objects": state.context.graph_objects(),
    }


@app.get("/admin/providers")
async def admin_providers() -> dict[str, Any]:
    return {"providers": [provider.model_dump() for provider in state.providers.providers]}


@app.get("/admin/agent-workers")
async def admin_agent_workers() -> dict[str, Any]:
    return {"agent_workers": [worker.model_dump() for worker in state.agents.agent_workers]}


@app.get("/admin/usage")
async def admin_usage(limit: int = 50) -> dict[str, Any]:
    return {"summary": state.ledger.summary(), "recent": state.ledger.recent_events(limit=limit)}


@app.get("/admin/circuits")
async def admin_circuits() -> dict[str, Any]:
    return {"circuits": state.circuits.snapshot()}


@app.get("/admin/agent-jobs")
async def admin_agent_jobs() -> dict[str, Any]:
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
                }
            )
    data.extend(
        [
            {"id": "auto/fast", "object": "model", "created": 0, "owned_by": "auto-router"},
            {"id": "auto/flash-start", "object": "model", "created": 0, "owned_by": "auto-router"},
            {"id": "auto/high-quality", "object": "model", "created": 0, "owned_by": "auto-router"},
            {"id": "auto/code", "object": "model", "created": 0, "owned_by": "auto-router"},
            {"id": "auto/local", "object": "model", "created": 0, "owned_by": "auto-router"},
            {"id": "auto/sophia", "object": "model", "created": 0, "owned_by": "auto-router"},
            {"id": "auto/backlog-burn", "object": "model", "created": 0, "owned_by": "auto-router"},
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
async def create_agent_job(request: Request) -> dict[str, Any]:
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


async def _execute(router_request: RouterRequest) -> JSONResponse | StreamingResponse:
    plan = state.policy_engine.plan(router_request)
    errors: list[str] = []
    for stage in plan.stages:
        if not stage.candidates and stage.optional:
            continue
        stage_rejections: list[str] = []
        for candidate in stage.candidates:
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
            estimate = state.quota.estimate(candidate.model, router_request.raw_body)
            if not state.quota.reserve(candidate.provider, candidate.model, estimate):
                rejection = f"quota unavailable for {candidate.provider.name}/{candidate.model.alias}"
                stage_rejections.append(rejection)
                errors.append(rejection)
                continue
            enqueue_route_decision_event(
                state,
                request=router_request,
                profile_name=plan.profile_name,
                stage=stage.purpose.value,
                chosen_candidate=candidate,
                candidates=stage.candidates,
                rejections=stage_rejections,
            )
            provider = build_provider(candidate.provider, timeout_seconds=get_settings().request_timeout_seconds)
            started_at_ms = int(time.time() * 1000)
            started = time.perf_counter()
            try:
                if router_request.stream and router_request.route in {"chat_completions", "responses", "completions"}:
                    gateway_context = _gateway_route_context(plan.profile_name, stage.purpose.value, router_request)
                    stream_response = await _dispatch_stream(provider, candidate, router_request, route_plan=gateway_context)
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    ended_at_ms = int(time.time() * 1000)
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
                    _record_usage(
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
                response = await _dispatch(provider, candidate, router_request, route_plan=gateway_context)
                latency_ms = int((time.perf_counter() - started) * 1000)
                state.circuits.record_success(owner)
                gateway_metadata = response.data.get("_gateway_metadata") if isinstance(response.data, dict) else None
                ended_at_ms = int(time.time() * 1000)
                _record_usage(
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
                latency_ms = int((time.perf_counter() - started) * 1000)
                state.quota.release(candidate.provider, candidate.model, estimate)
                state.circuits.record_failure(owner, str(exc), retry_after=_retry_after_seconds(exc))
                ended_at_ms = int(time.time() * 1000)
                _record_usage(
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
                latency_ms = int((time.perf_counter() - started) * 1000)
                state.quota.release(candidate.provider, candidate.model, estimate)
                state.circuits.record_failure(owner, str(exc))
                ended_at_ms = int(time.time() * 1000)
                _record_usage(
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
        return True
    privacy = str(metadata.get("privacy") or metadata.get("data_class") or "").strip().lower()
    if privacy in {"private", "personal", "internal", "secret", "sensitive", "local_only"}:
        return True
    if bool(metadata.get("sensitive")) or bool(metadata.get("private_data")):
        return True
    markers = metadata.get("markers") or metadata.get("tags") or []
    if isinstance(markers, str):
        markers = [markers]
    if isinstance(markers, list):
        normalized = {str(item).strip().lower() for item in markers if str(item).strip()}
        return bool(
            normalized
            & {
                "private",
                "personal",
                "internal",
                "local_only",
                "private_data",
                "internal_docs",
                "personal_docs",
                "voice_auth",
                "enrollment_sample",
                "signal",
                "credentials",
                "secret",
            }
        )
    return False


def _router_request(route: str, body: dict[str, Any]) -> RouterRequest:
    metadata_obj = body.get("metadata")
    metadata: dict[str, Any] = metadata_obj if isinstance(metadata_obj, dict) else {}
    priority_raw = metadata.get("priority") or body.get("priority") or Priority.interactive.value
    try:
        priority = Priority(str(priority_raw))
    except ValueError:
        priority = Priority.interactive
    allow_cloud = metadata.get("allow_cloud", body.get("allow_cloud"))
    task_id = metadata.get("task_id") or body.get("task_id")
    agent_run_id = metadata.get("agent_run_id") or body.get("agent_run_id")
    node_id = metadata.get("node_id") or body.get("node_id")
    model = body.get("model")
    forced_local = _request_privacy_forces_local(metadata, model if isinstance(model, str) else None)
    local_only = bool(metadata.get("local_only") or body.get("local_only") or forced_local)
    return RouterRequest(
        request_id=str(uuid.uuid4()),
        route=route,  # type: ignore[arg-type]
        model=model if isinstance(model, str) else None,
        messages=body.get("messages") or [],
        input=body.get("input"),
        max_tokens=body.get("max_tokens") if body.get("max_tokens") is not None else body.get("max_completion_tokens"),
        stream=bool(body.get("stream", False)),
        tools=body.get("tools"),
        response_format=body.get("response_format"),
        metadata=metadata,
        task_id=str(task_id) if isinstance(task_id, str) and task_id.strip() else None,
        agent_run_id=str(agent_run_id) if isinstance(agent_run_id, str) and agent_run_id.strip() else None,
        node_id=str(node_id) if isinstance(node_id, str) and node_id.strip() else None,
        priority=priority,
        local_only=local_only,
        allow_cloud=False if forced_local else (allow_cloud if isinstance(allow_cloud, bool) else None),
        raw_body=body,
    )


def _candidate_allowed(request: RouterRequest, candidate: ProviderCandidate, context: ContextSnapshot) -> bool:
    canonical_provider = context.canonical_provider_name(candidate.provider.name)
    context_provider = context.provider_for(canonical_provider)
    if context_provider and context_provider.is_blocked:
        return False
    lane = _lane_for_provider(candidate.provider, context_provider)
    if request.local_only or request.priority == Priority.local_only or request.allow_cloud is False or _request_privacy_forces_local(request.metadata, request.model):
        return lane == "local"
    return True


def _lane_for_provider(provider: Any, context_provider: Any | None) -> str:
    if context_provider is not None:
        return str(context_provider.lane)
    if str(provider.quota_class) == "local" or provider.type == "lmstudio":
        return "local"
    return "free_api"


def _owner(candidate: ProviderCandidate) -> str:
    return f"{candidate.provider.name}/{candidate.model.alias}"


def _retry_after_seconds(exc: ProviderError) -> int | None:
    if exc.retry_after_seconds is not None:
        return exc.retry_after_seconds
    if exc.status_code == 429:
        return 120
    return None


def _record_usage(
    request: RouterRequest,
    provider: str,
    model: str,
    stage: str,
    estimate: Any,
    status_code: int | None,
    latency_ms: int,
    usage: dict[str, int] | None = None,
    error: Exception | None = None,
    gateway_metadata: dict[str, Any] | None = None,
    started_at_ms: int | None = None,
    ended_at_ms: int | None = None,
) -> None:
    usage = usage or {}
    runtime = _build_runtime_sample(
        request=request,
        provider=provider,
        model=model,
        stage=stage,
        estimate=estimate,
        status_code=status_code,
        latency_ms=latency_ms,
        usage=usage,
        error=error,
        gateway_metadata=gateway_metadata,
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
    )
    state.ledger.record(
        UsageEvent(
            request_id=request.request_id,
            provider_id=provider,
            model_id=model,
            route=request.route,
            priority=request.priority.value,
            stage=stage,
            input_tokens=runtime.input_tokens,
            output_tokens=runtime.output_tokens,
            total_tokens=runtime.total_tokens,
            quota_units=estimate.dimensions,
            status_code=status_code,
            latency_ms=latency_ms,
            error_type=type(error).__name__ if error else None,
            error_message=str(error)[:1000] if error else None,
        )
    )
    state.ledger.record_runtime_sample(runtime)


def _build_runtime_sample(
    request: RouterRequest,
    provider: str,
    model: str,
    stage: str,
    estimate: Any,
    status_code: int | None,
    latency_ms: int,
    usage: dict[str, int],
    error: Exception | None,
    gateway_metadata: dict[str, Any] | None,
    started_at_ms: int | None,
    ended_at_ms: int | None,
) -> RuntimeSample:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    gateway_metadata = gateway_metadata if isinstance(gateway_metadata, dict) else {}
    started_at_ms = _coerce_ms(started_at_ms)
    ended_at_ms = _coerce_ms(ended_at_ms)
    queue_wait_ms = _derive_queue_wait_ms(metadata, started_at_ms)
    load_time_ms = _derive_load_time_ms(metadata, gateway_metadata)
    input_tokens = int(usage.get("prompt_tokens") or getattr(estimate, "input_tokens", 0) or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or getattr(estimate, "total_tokens", 0) or 0)
    elapsed_ms = latency_ms if latency_ms is not None else None
    if elapsed_ms is None and started_at_ms is not None and ended_at_ms is not None:
        elapsed_ms = max(ended_at_ms - started_at_ms, 0)
    elapsed_seconds = max((elapsed_ms or 0) / 1000.0, 0.001)
    tokens_per_second = round(total_tokens / elapsed_seconds, 3) if total_tokens else 0.0
    value_units = output_tokens or total_tokens
    value_per_second = round(value_units / elapsed_seconds, 3) if value_units else 0.0
    return RuntimeSample(
        request_id=request.request_id,
        provider_id=provider,
        model_id=model,
        route=request.route,
        priority=request.priority.value,
        stage=stage,
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
        queue_wait_ms=queue_wait_ms,
        load_time_ms=load_time_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        tokens_per_second=tokens_per_second,
        value_units=value_units,
        value_per_second=value_per_second,
        status_code=status_code,
        latency_ms=latency_ms,
        error_type=type(error).__name__ if error else None,
        error_message=str(error)[:1000] if error else None,
    )


def _coerce_ms(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_epoch_ms(value: Any) -> int | None:
    coerced = _coerce_ms(value)
    if coerced is None:
        return None
    if coerced < 10_000_000_000:
        return coerced * 1000
    return coerced


def _derive_queue_wait_ms(metadata: dict[str, Any], started_at_ms: int | None) -> int | None:
    explicit = _coerce_ms(
        metadata.get("queue_wait_ms")
        or metadata.get("queue_ms")
        or metadata.get("wait_ms")
        or metadata.get("wait_time_ms")
    )
    if explicit is not None:
        return explicit
    if started_at_ms is None:
        return None
    queued_at = metadata.get("queued_at_ms") or metadata.get("queued_at") or metadata.get("enqueued_at_ms") or metadata.get("enqueued_at")
    queued_at_ms = _coerce_epoch_ms(queued_at)
    if queued_at_ms is None:
        return None
    return max(started_at_ms - queued_at_ms, 0)


def _derive_load_time_ms(metadata: dict[str, Any], gateway_metadata: dict[str, Any]) -> int | None:
    for source in (gateway_metadata, metadata):
        explicit = _coerce_ms(
            source.get("load_time_ms")
            or source.get("load_ms")
            or source.get("prompt_load_ms")
        )
        if explicit is not None:
            return explicit
        started_at = source.get("load_started_at_ms") or source.get("load_started_at")
        ended_at = source.get("load_completed_at_ms") or source.get("load_completed_at")
        started_at_ms = _coerce_epoch_ms(started_at)
        ended_at_ms = _coerce_epoch_ms(ended_at)
        if started_at_ms is not None and ended_at_ms is not None:
            return max(ended_at_ms - started_at_ms, 0)
    return None


async def _provider_health_reports() -> list[dict[str, Any]]:
    providers = state.providers.enabled()
    checks = await asyncio.gather(
        *(build_provider(provider, timeout_seconds=get_settings().request_timeout_seconds).health() for provider in providers),
        return_exceptions=True,
    )
    reports: list[dict[str, Any]] = []
    open_owners = {item["owner"] for item in state.circuits.snapshot() if item["open"]}
    for provider, health_result in zip(providers, checks, strict=False):
        if isinstance(health_result, ProviderHealth):
            report = health_result.model_dump()
        else:
            report = {"provider": provider.name, "ok": False, "detail": str(health_result), "metadata": {}}
        provider_open = [owner for owner in open_owners if owner.startswith(f"{provider.name}/")]
        if provider_open:
            report["ok"] = False
            report["detail"] = f"Circuit open: {', '.join(provider_open[:3])}"
        reports.append(report)
        if hasattr(state, "signal_registry"):
            signals = provider_health_signals(provider.name, report, node_id=provider.node_id)
            state.signal_registry.save_snapshot(signal_snapshot(signals, revision=f"provider-health:{provider.name}", source="provider_health"))
    if hasattr(state, "signal_registry"):
        state.context = state.signal_registry.hydrate_context(state.context)
        if hasattr(state, "policy_engine"):
            state.policy_engine.context = state.context
    return reports


def _normalize_response_payload(response: ProviderResponse, stage: str, profile_name: str) -> dict[str, Any]:
    payload = dict(response.data)
    payload["model"] = response.model
    payload["auto_router"] = {
        "provider": response.provider,
        "model": response.model,
        "stage": str(stage),
        "profile": profile_name,
    }
    if "usage" not in payload and response.usage is not None:
        payload["usage"] = response.usage
    return payload


def run() -> None:
    settings = get_settings()
    uvicorn.run("auto_router.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
