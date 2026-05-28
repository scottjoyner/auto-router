from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from auto_router.config import (
    AgentWorkerRegistry,
    PolicyRegistry,
    ProviderRegistry,
    load_agent_worker_registry,
    load_policy_registry,
    load_provider_registry,
)
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


state = AppState()


def load_state() -> None:
    settings = get_settings()
    state.providers = load_provider_registry(settings.provider_config)
    state.policies = load_policy_registry(settings.policy_config)
    state.agents = load_agent_worker_registry(settings.agent_config)
    state.policy_engine = PolicyEngine(state.providers, state.policies, settings.default_profile)
    state.quota = InMemoryQuotaManager()


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
    return {
        "ok": True,
        "service": "auto-router",
        "providers_enabled": configured,
        "agent_workers_configured": len(state.agents.agent_workers),
        "time": int(time.time()),
    }


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    snapshots = state.quota.snapshots(state.providers.enabled())
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
    return "\n".join(lines) + "\n"


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> str:
    snapshots = state.quota.snapshots(state.providers.enabled())
    rows = []
    for snapshot in snapshots:
        for dimension, values in snapshot.dimensions.items():
            rows.append(
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
    return f"""
    <!doctype html>
    <html>
      <head>
        <title>auto-router dashboard</title>
        <style>
          body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
          table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
          th, td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; }}
          th {{ background: #f5f5f5; }}
          .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 1rem; margin-bottom: 1rem; }}
        </style>
      </head>
      <body>
        <h1>auto-router</h1>
        <div class="card">
          <strong>Enabled providers:</strong> {len(state.providers.enabled())}<br>
          <strong>Configured agent workers:</strong> {len(state.agents.agent_workers)}
        </div>
        <h2>Quota</h2>
        <table>
          <thead><tr><th>Provider</th><th>Model</th><th>Dimension</th><th>Limit</th><th>Used</th><th>Remaining</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
        <h2>Agent Workers</h2>
        <table>
          <thead><tr><th>Name</th><th>Type</th><th>Enabled</th></tr></thead>
          <tbody>{''.join(agent_rows)}</tbody>
        </table>
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
            if not _candidate_allowed(router_request, candidate):
                continue
            estimate = state.quota.estimate(candidate.model, router_request.raw_body)
            if not state.quota.reserve(candidate.provider, candidate.model, estimate):
                errors.append(f"quota unavailable for {candidate.provider.name}/{candidate.model.alias}")
                continue
            provider = build_provider(candidate.provider, timeout_seconds=get_settings().request_timeout_seconds)
            try:
                response = await _dispatch(provider, candidate, router_request)
                payload = dict(response.data)
                payload.setdefault("auto_router", {})
                payload["auto_router"].update(
                    {
                        "provider": response.provider,
                        "provider_model": response.model,
                        "stage": stage.purpose,
                        "profile": plan.profile_name,
                    }
                )
                return JSONResponse(payload, status_code=response.status_code)
            except ProviderError as exc:
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


def run() -> None:
    settings = get_settings()
    uvicorn.run("auto_router.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
