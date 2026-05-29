from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from auto_router.circuit_breaker import CircuitBreakerManager
from auto_router.config import (
    AgentWorkerRegistry,
    PolicyRegistry,
    ProviderRegistry,
    load_agent_worker_registry,
    load_policy_registry,
    load_provider_registry,
)
from auto_router.ledger import UsageEvent, UsageLedger
from auto_router.models import Priority, ProviderCandidate, RouterRequest
from auto_router.policy import PolicyEngine
from auto_router.providers import ProviderError, build_provider
from auto_router.quota import InMemoryQuotaManager
from auto_router.settings import get_settings


class AppState:
    providers: ProviderRegistry
    policies: PolicyRegistry
    agents: AgentWorkerRegistry
    policy_engine: PolicyEngine
    quota: InMemoryQuotaManager
    ledger: UsageLedger
    circuits: CircuitBreakerManager


state = AppState()


def load_state() -> None:
    settings = get_settings()
    state.providers = load_provider_registry(settings.provider_config)
    state.policies = load_policy_registry(settings.policy_config)
    state.agents = load_agent_worker_registry(settings.agent_config)
    state.policy_engine = PolicyEngine(state.providers, state.policies, settings.default_profile)
    state.quota = InMemoryQuotaManager()
    state.ledger = UsageLedger(settings.database_url)
    state.circuits = CircuitBreakerManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_state()
    yield


app = FastAPI(
    title="auto-router",
    version="0.1.0",
    description="Local-first OpenAI-compatible LLM router with free quota scheduling.",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    configured = sum(1 for provider in state.providers.enabled())
    open_circuits = [circuit for circuit in state.circuits.snapshot() if circuit["open"]]
    return {
        "ok": True,
        "service": "auto-router",
        "providers_enabled": configured,
        "agent_workers_configured": len(state.agents.agent_workers),
        "open_circuits": len(open_circuits),
        "time": int(time.time()),
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
async def dashboard() -> str:
    snapshots = state.quota.snapshots(state.providers.enabled())
    usage_summary = state.ledger.summary()
    recent_events = state.ledger.recent_events(limit=15)
    circuit_snapshot = state.circuits.snapshot()

    quota_rows = []
    for snapshot in snapshots:
        for dimension, values in snapshot.dimensions.items():
            quota_rows.append(
                "<tr>"
                f"<td>{snapshot.provider}</td>"
                f"<td>{snapshot.model}</td>"
                f"<td>{dimension}</td>"
                f"<td>{values.get('limit')}</td>"
                f"<td>{values.get('used')}</td>"
                f"<td>{values.get('remaining')}</td>"
                "</tr>"
            )
    agent_rows = [
        f"<tr><td>{worker.name}</td><td>{worker.type}</td><td>{worker.enabled}</td></tr>"
        for worker in state.agents.agent_workers
    ]
    circuit_rows = [
        "<tr>"
        f"<td>{circuit['owner']}</td>"
        f"<td>{circuit['open']}</td>"
        f"<td>{circuit['failures']}</td>"
        f"<td>{circuit['opened_until']}</td>"
        f"<td>{circuit['last_error'] or ''}</td>"
        "</tr>"
        for circuit in circuit_snapshot
    ]
    event_rows = [
        "<tr>"
        f"<td>{event['created_at']}</td>"
        f"<td>{event['provider_id']}</td>"
        f"<td>{event['model_id']}</td>"
        f"<td>{event['route']}</td>"
        f"<td>{event['priority']}</td>"
        f"<td>{event['stage']}</td>"
        f"<td>{event['status_code']}</td>"
        f"<td>{event['latency_ms']}</td>"
        f"<td>{event['error_type'] or ''}</td>"
        "</tr>"
        for event in recent_events
    ]
    totals = usage_summary.get("totals", {})
    return f"""
    <!doctype html>
    <html>
      <head>
        <title>auto-router dashboard</title>
        <style>
          body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
          table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
          th, td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; vertical-align: top; }}
          th {{ background: #f5f5f5; }}
          .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; }}
          .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 1rem; margin-bottom: 1rem; }}
          .muted {{ color: #666; }}
        </style>
      </head>
      <body>
        <h1>auto-router</h1>
        <div class="grid">
          <div class="card"><strong>Enabled providers</strong><br>{len(state.providers.enabled())}</div>
          <div class="card"><strong>Agent workers</strong><br>{len(state.agents.agent_workers)}</div>
          <div class="card"><strong>Usage events</strong><br>{totals.get('requests', 0)}</div>
          <div class="card"><strong>Total tokens</strong><br>{totals.get('total_tokens', 0)}</div>
          <div class="card"><strong>Open circuits</strong><br>{sum(1 for c in circuit_snapshot if c['open'])}</div>
        </div>
        <h2>Quota</h2>
        <table>
          <thead><tr><th>Provider</th><th>Model</th><th>Dimension</th><th>Limit</th><th>Used</th><th>Remaining</th></tr></thead>
          <tbody>{''.join(quota_rows)}</tbody>
        </table>
        <h2>Circuits</h2>
        <table>
          <thead><tr><th>Owner</th><th>Open</th><th>Failures</th><th>Opened Until</th><th>Last Error</th></tr></thead>
          <tbody>{''.join(circuit_rows)}</tbody>
        </table>
        <h2>Recent Usage</h2>
        <table>
          <thead><tr><th>Created</th><th>Provider</th><th>Model</th><th>Route</th><th>Priority</th><th>Stage</th><th>Status</th><th>Latency ms</th><th>Error</th></tr></thead>
          <tbody>{''.join(event_rows)}</tbody>
        </table>
        <h2>Agent Workers</h2>
        <table>
          <thead><tr><th>Name</th><th>Type</th><th>Enabled</th></tr></thead>
          <tbody>{''.join(agent_rows)}</tbody>
        </table>
        <p class="muted">Prompt bodies are not stored in the usage ledger.</p>
      </body>
    </html>
    """


@app.get("/admin/quota")
async def admin_quota() -> dict[str, Any]:
    return {"providers": [snapshot.model_dump() for snapshot in state.quota.snapshots(state.providers.enabled())]}


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


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    data = []
    for provider in state.providers.enabled():
        for model in provider.models:
            data.append(
                {
                    "id": model.alias,
                    "object": "model",
                    "created": 0,
                    "owned_by": provider.name,
                    "provider_model": model.provider_model,
                    "capabilities": sorted(model.capabilities),
                }
            )
    data.extend(
        [
            {"id": "auto/fast", "object": "model", "created": 0, "owned_by": "auto-router"},
            {"id": "auto/high-quality", "object": "model", "created": 0, "owned_by": "auto-router"},
            {"id": "auto/code", "object": "model", "created": 0, "owned_by": "auto-router"},
            {"id": "auto/local", "object": "model", "created": 0, "owned_by": "auto-router"},
        ]
    )
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> JSONResponse:
    body = await request.json()
    router_request = _router_request("chat_completions", body)
    return await _execute(router_request)


@app.post("/v1/responses")
async def responses(request: Request) -> JSONResponse:
    body = await request.json()
    router_request = _router_request("responses", body)
    return await _execute(router_request)


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> JSONResponse:
    body = await request.json()
    router_request = _router_request("embeddings", body)
    router_request.required_capabilities.add("embeddings")
    return await _execute(router_request)


@app.post("/v1/completions")
async def completions(request: Request) -> JSONResponse:
    body = await request.json()
    router_request = _router_request("completions", body)
    return await _execute(router_request)


@app.post("/jobs/agent")
async def create_agent_job(request: Request) -> dict[str, Any]:
    body = await request.json()
    return {
        "job_id": str(uuid.uuid4()),
        "status": "queued_stub",
        "detail": "Agent worker execution will be implemented after API routing MVP.",
        "request": body,
    }


async def _execute(router_request: RouterRequest) -> JSONResponse:
    plan = state.policy_engine.plan(router_request)
    errors: list[str] = []

    for stage in plan.stages:
        if not stage.candidates and stage.optional:
            continue
        for candidate in stage.candidates:
            owner = _owner(candidate)
            if not _candidate_allowed(router_request, candidate):
                continue
            if not state.circuits.allowed(owner):
                errors.append(f"circuit open for {owner}")
                continue

            estimate = state.quota.estimate(candidate.model, router_request.raw_body)
            if not state.quota.reserve(candidate.provider, candidate.model, estimate):
                errors.append(f"quota unavailable for {candidate.provider.name}/{candidate.model.alias}")
                continue

            provider = build_provider(candidate.provider, timeout_seconds=get_settings().request_timeout_seconds)
            started = time.perf_counter()
            try:
                response = await _dispatch(provider, candidate, router_request)
                latency_ms = int((time.perf_counter() - started) * 1000)
                state.circuits.record_success(owner)
                state.ledger.record(
                    UsageEvent(
                        request_id=router_request.request_id,
                        provider_id=response.provider,
                        model_id=response.model,
                        route=router_request.route,
                        priority=router_request.priority.value,
                        stage=stage.purpose.value,
                        input_tokens=int(response.usage.get("prompt_tokens") or estimate.input_tokens),
                        output_tokens=int(response.usage.get("completion_tokens") or 0),
                        total_tokens=int(response.usage.get("total_tokens") or estimate.total_tokens),
                        quota_units=estimate.dimensions,
                        status_code=response.status_code,
                        latency_ms=latency_ms,
                    )
                )
                payload = dict(response.data)
                payload.setdefault("auto_router", {})
                payload["auto_router"].update(
                    {
                        "provider": response.provider,
                        "provider_model": response.model,
                        "stage": stage.purpose.value,
                        "profile": plan.profile_name,
                        "latency_ms": latency_ms,
                    }
                )
                return JSONResponse(payload, status_code=response.status_code)
            except ProviderError as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                state.circuits.record_failure(owner, str(exc), retry_after=_retry_after_seconds(exc))
                state.ledger.record(
                    UsageEvent(
                        request_id=router_request.request_id,
                        provider_id=candidate.provider.name,
                        model_id=candidate.model.provider_model,
                        route=router_request.route,
                        priority=router_request.priority.value,
                        stage=stage.purpose.value,
                        input_tokens=estimate.input_tokens,
                        output_tokens=0,
                        total_tokens=estimate.total_tokens,
                        quota_units=estimate.dimensions,
                        status_code=exc.status_code,
                        latency_ms=latency_ms,
                        error_type=type(exc).__name__,
                        error_message=str(exc)[:1000],
                    )
                )
                errors.append(str(exc))
                if not exc.retryable:
                    break

    raise HTTPException(status_code=503, detail={"error": "all providers failed", "details": errors})


async def _dispatch(provider: Any, candidate: ProviderCandidate, request: RouterRequest):
    provider_model = candidate.model.provider_model
    if request.route == "chat_completions":
        return await provider.chat_completions(request, provider_model)
    if request.route == "responses":
        return await provider.responses(request, provider_model)
    if request.route == "embeddings":
        return await provider.embeddings(request, provider_model)
    if request.route == "completions":
        return await provider.completions(request, provider_model)
    raise ProviderError(f"unsupported route {request.route}", retryable=False)


def _router_request(route: str, body: dict[str, Any]) -> RouterRequest:
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    priority = metadata.get("priority") or body.get("priority") or Priority.interactive
    local_only = bool(metadata.get("local_only") or body.get("local_only"))
    model = body.get("model")
    return RouterRequest(
        request_id=str(uuid.uuid4()),
        route=route,  # type: ignore[arg-type]
        model=model,
        messages=body.get("messages") or [],
        input=body.get("input"),
        max_tokens=body.get("max_tokens") or body.get("max_completion_tokens"),
        stream=bool(body.get("stream", False)),
        tools=body.get("tools"),
        response_format=body.get("response_format"),
        metadata=metadata,
        priority=Priority(priority) if priority in Priority._value2member_map_ else Priority.interactive,
        local_only=local_only,
        raw_body=body,
    )


def _candidate_allowed(request: RouterRequest, candidate: ProviderCandidate) -> bool:
    if request.local_only or request.priority == Priority.local_only:
        return str(candidate.provider.quota_class) == "local"
    return True


def _owner(candidate: ProviderCandidate) -> str:
    return f"{candidate.provider.name}/{candidate.model.alias}"


def _retry_after_seconds(exc: ProviderError) -> int | None:
    if exc.retry_after_seconds is not None:
        return exc.retry_after_seconds
    if exc.status_code == 429:
        return 120
    return None


def run() -> None:
    settings = get_settings()
    uvicorn.run("auto_router.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
