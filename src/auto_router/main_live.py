# ruff: noqa: E402
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import Body, Depends, FastAPI, HTTPException
from starlette.responses import JSONResponse, StreamingResponse

from auto_router.offline_guard import (
    enforce_strict_offline_provider_config,
    strict_offline_enabled,
)

# This fleet deployment is intentionally offline-only. Do not permit an environment
# override to silently re-enable public inference providers.
if not strict_offline_enabled():
    raise RuntimeError(
        "AUTO_ROUTER_STRICT_OFFLINE cannot be disabled in the reconciled fleet deployment"
    )

# Validate before importing the application module, because application import builds
# provider state. The imports below are intentionally late; moving them above this
# guard would permit invalid provider state to be constructed before validation.
enforce_strict_offline_provider_config()

import auto_router.assistx_routes as assistx_routes_module
import auto_router.main as main_module
from auto_router.access_paths import AccessPathChoice, RuntimeAccessPathSelector
from auto_router.admission import RuntimeAdmissionController, RuntimeAdmissionLease
from auto_router.assistx_routes import register_assistx_routes
from auto_router.claim_fence import assert_executor_claim_current
from auto_router.fleet_routes import router as fleet_router
from auto_router.main import app, state
from auto_router.memory_routes import register_memory_routes
from auto_router.models import ProviderCandidate, ProviderResponse, RouterRequest
from auto_router.ops_dashboard_routes import register_ops_dashboard_routes
from auto_router.otel import init_otel
from auto_router.providers import ProviderStreamResponse, build_provider
from auto_router.route_event_patch import install_route_event_patch
from auto_router.runtime_projection import (
    RuntimeProjectionManager,
    projection_poll_task,
)
from auto_router.security import require_admin
from auto_router.settings import get_settings
from auto_router.strict_assistx_routes import install_strict_assistx_route_guard

_RETIRED_INHERITED_PATHS = {
    "/jobs/agent",
}

_ORIGINAL_DISPATCH = main_module._dispatch
_ORIGINAL_DISPATCH_STREAM = main_module._dispatch_stream
_ORIGINAL_EXECUTE = main_module._execute


async def _cancel_tasks(tasks: list[asyncio.Task[object]]) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


def _remove_retired_inherited_routes() -> None:
    app.router.routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) not in _RETIRED_INHERITED_PATHS
    ]


def _admission_controller() -> RuntimeAdmissionController:
    controller = getattr(state, "admission", None)
    if not isinstance(controller, RuntimeAdmissionController):
        raise RuntimeError("runtime admission controller is not initialized")
    return controller


def _access_path_selector() -> RuntimeAccessPathSelector:
    selector = getattr(state, "access_paths", None)
    if not isinstance(selector, RuntimeAccessPathSelector):
        raise RuntimeError("runtime access path selector is not initialized")
    return selector


def _projection_manager() -> RuntimeProjectionManager:
    manager = getattr(state, "runtime_projection_manager", None)
    if not isinstance(manager, RuntimeProjectionManager):
        raise RuntimeError("runtime projection manager is not initialized")
    return manager


def _annotate_request_telemetry(
    request: RouterRequest,
    candidate: ProviderCandidate,
    choice: AccessPathChoice,
) -> None:
    """Attach non-secret physical runtime facts for the durable AssistX event stream."""

    provider = candidate.provider
    model = candidate.model
    manager = getattr(state, "runtime_projection_manager", None)
    current = manager.current if isinstance(manager, RuntimeProjectionManager) else None
    request.metadata = {
        **(request.metadata if isinstance(request.metadata, dict) else {}),
        "runtime_projection_generation": current.generation if current else 0,
        "runtime_projection_revision": current.revision if current else "bootstrap",
        "runtime_projection_checksum": current.checksum if current else None,
        "runtime_projection_expires_at_ms": (
            current.expires_at_ms if current else None
        ),
        "runtime_node_id": provider.node_id,
        "runtime_instance_id": choice.runtime_instance_id,
        "runtime_kind": provider.runtime_kind or provider.type,
        "runtime_version": provider.runtime_version,
        "headless": provider.headless,
        "selected_transport": choice.transport,
        "selected_access_url": choice.base_url,
        "parallel_slots": provider.parallel_slots,
        "queue_limit": provider.queue_limit,
        "queue_timeout_seconds": provider.queue_timeout_seconds,
        "model_instance_id": model.model_instance_id,
        "model_key": model.alias,
        "provider_model": model.provider_model,
        "artifact_fingerprint": model.artifact_fingerprint,
        "quantization": model.quantization,
        "context_length": model.context_window,
    }


async def _select_provider(
    candidate: ProviderCandidate,
) -> tuple[Any, ProviderCandidate, AccessPathChoice]:
    """Select an approved path while preserving the physical runtime identity."""

    choice = await _access_path_selector().select(candidate)
    selected_config = candidate.provider.model_copy(update={"base_url": choice.base_url})
    selected_candidate = candidate.model_copy(update={"provider": selected_config})
    settings = get_settings()
    selected_provider = build_provider(
        selected_config,
        timeout_seconds=settings.attempt_timeout_seconds,
        connect_timeout_seconds=settings.connect_timeout_seconds,
    )
    return selected_provider, selected_candidate, choice


async def _admitted_dispatch(
    _provider: Any,
    candidate: ProviderCandidate,
    request: RouterRequest,
    route_plan: Any | None = None,
) -> ProviderResponse:
    # No request may acquire capacity from a stale or absent AssistX generation.
    # Existing leases acquired while fresh can still finish and release safely.
    _projection_manager().assert_current_fresh()
    lease = await _admission_controller().acquire(candidate)
    try:
        # Revalidate after the queue wait so a revoked/expired task cannot consume
        # a model even if it entered admission while its claim was still valid.
        await assert_executor_claim_current(request, state)
        provider, selected_candidate, choice = await _select_provider(candidate)
        _annotate_request_telemetry(request, selected_candidate, choice)
        return await _ORIGINAL_DISPATCH(
            provider,
            selected_candidate,
            request,
            route_plan=route_plan,
        )
    finally:
        await lease.release()


async def _release_stream_lease(
    body: AsyncIterator[bytes],
    lease: RuntimeAdmissionLease,
) -> AsyncIterator[bytes]:
    try:
        async for chunk in body:
            yield chunk
    finally:
        await lease.release()


async def _admitted_dispatch_stream(
    _provider: Any,
    candidate: ProviderCandidate,
    request: RouterRequest,
    route_plan: Any | None = None,
) -> ProviderStreamResponse:
    _projection_manager().assert_current_fresh()
    lease = await _admission_controller().acquire(candidate)
    try:
        await assert_executor_claim_current(request, state)
        provider, selected_candidate, choice = await _select_provider(candidate)
        _annotate_request_telemetry(request, selected_candidate, choice)
        response = await _ORIGINAL_DISPATCH_STREAM(
            provider,
            selected_candidate,
            request,
            route_plan=route_plan,
        )
    except BaseException:
        await lease.release()
        raise

    response.body = _release_stream_lease(response.body, lease)
    return response


async def _execute_with_access_paths(router_request: RouterRequest) -> JSONResponse | StreamingResponse:
    """Execute a request using the access path selector for approved runtime paths."""
    # Feed the router's probe-history health into the policy engine so routing
    # can demote/exclude flaky + unloaded nodes (liveness-gated routing).
    state.policy_engine.provider_health = main_module._fleet_health_map()
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
            owner = main_module._owner(candidate)
            if not main_module._candidate_allowed(router_request, candidate, state.context):
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
                main_module.enqueue_route_decision_event,
                state,
                request=router_request,
                profile_name=plan.profile_name,
                stage=stage.purpose.value,
                chosen_candidate=candidate,
                candidates=stage.candidates,
                rejections=stage_rejections,
            )
            state.policy_engine.mark_inflight_start(owner)
            # Use access path selector to get approved URL
            _projection_manager().assert_current_fresh()
            lease = await _admission_controller().acquire(candidate)
            try:
                # Only validate executor claim for requests that carry executor metadata
                metadata = router_request.metadata if isinstance(router_request.metadata, dict) else {}
                if metadata.get("assistx_executor"):
                    await assert_executor_claim_current(router_request, state)
                provider, selected_candidate, choice = await _select_provider(candidate)
                _annotate_request_telemetry(router_request, selected_candidate, choice)
                started_at_ms = int(time.time() * 1000)
                started = time.perf_counter()
                try:
                    if router_request.stream and router_request.route in {"chat_completions", "responses", "completions"}:
                        gateway_context = main_module._gateway_route_context(plan.profile_name, stage.purpose.value, router_request)
                        stream_response = await asyncio.wait_for(
                            _dispatch_stream_with_choice(provider, selected_candidate, router_request, route_plan=gateway_context),
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
                            main_module._record_usage,
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
                    gateway_context = main_module._gateway_route_context(plan.profile_name, stage.purpose.value, router_request)
                    response = await asyncio.wait_for(
                        _dispatch_with_choice(provider, selected_candidate, router_request, route_plan=gateway_context),
                        timeout=settings.attempt_timeout_seconds,
                    )
                    state.policy_engine.mark_inflight_end(owner)
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    state.policy_engine.mark_latency(owner, latency_ms)
                    state.circuits.record_success(owner)
                    await asyncio.to_thread(
                        main_module._record_usage,
                        router_request,
                        response.provider,
                        response.model,
                        stage.purpose.value,
                        estimate,
                        response.status_code,
                        latency_ms,
                        started_at_ms=started_at_ms,
                        ended_at_ms=int(time.time() * 1000),
                    )
                    return JSONResponse(
                        content=response.data,
                        status_code=response.status_code,
                        headers={
                            "x-auto-router-provider": response.provider,
                            "x-auto-router-model": response.model,
                            "x-auto-router-stage": stage.purpose.value,
                            "x-auto-router-profile": plan.profile_name,
                        },
                    )
                except asyncio.TimeoutError:
                    state.policy_engine.mark_inflight_end(owner)
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    state.policy_engine.mark_latency(owner, latency_ms)
                    state.circuits.record_failure(owner, "timeout")
                    errors.append(f"timeout for {owner}")
                    continue
                except Exception as exc:
                    state.policy_engine.mark_inflight_end(owner)
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    state.policy_engine.mark_latency(owner, latency_ms)
                    state.circuits.record_failure(owner, str(exc))
                    errors.append(f"{type(exc).__name__}: {exc}")
                    continue
            finally:
                await lease.release()
    # All candidates exhausted
    if timed_out:
        raise HTTPException(status_code=504, detail={"error": "request deadline exceeded", "errors": errors})
    raise HTTPException(status_code=503, detail={"error": "no available provider", "errors": errors})


async def _dispatch_with_choice(
    provider: Any,
    candidate: ProviderCandidate,
    request: RouterRequest,
    route_plan: Any | None = None,
) -> ProviderResponse:
    provider_model = candidate.model.provider_model
    if request.route == "chat_completions":
        if isinstance(provider, main_module.AgentGatewayProviderAdapter):
            return await provider.chat_completions(request, provider_model, route_plan=route_plan)
        return await provider.chat_completions(request, provider_model)
    if request.route == "responses":
        if isinstance(provider, main_module.AgentGatewayProviderAdapter):
            return await provider.responses(request, provider_model)
        return await provider.responses(request, provider_model)
    if request.route == "embeddings":
        return await provider.embeddings(request, provider_model)
    if request.route == "completions":
        if isinstance(provider, main_module.AgentGatewayProviderAdapter):
            return await provider.completions(request, provider_model)
        return await provider.completions(request, provider_model)
    raise main_module.ProviderError(f"unsupported route {request.route}", retryable=False)


async def _dispatch_stream_with_choice(
    provider: Any,
    candidate: ProviderCandidate,
    request: RouterRequest,
    route_plan: Any | None = None,
) -> ProviderStreamResponse:
    provider_model = candidate.model.provider_model
    if request.route == "chat_completions":
        if isinstance(provider, main_module.AgentGatewayProviderAdapter):
            return await provider.stream_chat_completions(request, provider_model, route_plan=route_plan)
        return await provider.stream_chat_completions(request, provider_model)
    if request.route == "responses":
        if isinstance(provider, main_module.AgentGatewayProviderAdapter):
            return await provider.stream_responses(request, provider_model)
        return await provider.stream_responses(request, provider_model)
    if request.route == "completions":
        if isinstance(provider, main_module.AgentGatewayProviderAdapter):
            return await provider.stream_completions(request, provider_model)
        return await provider.stream_completions(request, provider_model)
    raise main_module.ProviderError(f"unsupported stream route {request.route}", retryable=False)


@asynccontextmanager
async def strict_offline_lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start only AssistX projection, admission control, and local housekeeping loops.

    The bootstrap YAML is health-only and has zero capacity/models. AssistX publishes
    signed, monotonic runtime generations plus refreshable short-lived leases. Each
    new generation is prepared fully before atomically replacing provider registry,
    context, policy, admission, and access-path state. Active old-generation leases
    retain their original gate objects until completion.
    """

    await main_module.load_state()
    providers = state.providers.enabled()
    state.admission = RuntimeAdmissionController(providers)
    state.access_paths = RuntimeAccessPathSelector(
        providers,
        cache_ttl_seconds=float(
            os.getenv("AUTO_ROUTER_ACCESS_PATH_TTL_SECONDS", "15")
        ),
        probe_timeout_seconds=float(
            os.getenv("AUTO_ROUTER_ACCESS_PATH_PROBE_TIMEOUT_SECONDS", "2")
        ),
    )
    state.runtime_projection_manager = RuntimeProjectionManager(state)
    init_otel()
    tasks: list[asyncio.Task[object]] = [
        asyncio.create_task(main_module.refresh_context_task()),
        asyncio.create_task(main_module.outbox_dispatch_task()),
        asyncio.create_task(main_module.prune_task()),
        asyncio.create_task(main_module.persist_latency_task()),
    ]
    if os.getenv("AUTO_ROUTER_RUNTIME_PROJECTION_URL", "").strip():
        tasks.append(
            asyncio.create_task(
                projection_poll_task(state, state.runtime_projection_manager)
            )
        )
    try:
        yield
    finally:
        await _cancel_tasks(tasks)
        try:
            if hasattr(state, "policy_engine"):
                await asyncio.to_thread(state.policy_engine.persist_latency, True)
        except Exception:
            pass


main_module._dispatch = _admitted_dispatch
main_module._dispatch_stream = _admitted_dispatch_stream
main_module._execute = _execute_with_access_paths
install_route_event_patch(main_module)
install_strict_assistx_route_guard(assistx_routes_module)
_remove_retired_inherited_routes()
app.router.lifespan_context = strict_offline_lifespan
app.state.router_state = state

# Register only operator visibility and AssistX-owned integration surfaces. Legacy
# live-model/service/CLI discovery, backlog scheduling, and in-process agent routes
# remain in history but are not mounted by the reconciled runtime entrypoint.
register_ops_dashboard_routes(app, state)
register_assistx_routes(app, state)
register_memory_routes(app, state)
app.include_router(fleet_router)


@app.get("/admin/admission")
async def admission_status(
    _: None = Depends(require_admin),
) -> dict[str, Any]:
    """Expose ephemeral capacity, path state, and projection generation."""

    manager = getattr(state, "runtime_projection_manager", None)
    return {
        "runtimes": _admission_controller().snapshot(),
        "access_paths": _access_path_selector().snapshot(),
        "runtime_projection": (
            manager.status()
            if isinstance(manager, RuntimeProjectionManager)
            else {"configured": False, "fresh": False}
        ),
    }


@app.get("/admin/runtime-projection")
async def runtime_projection_status(
    _: None = Depends(require_admin),
) -> dict[str, Any]:
    return _projection_manager().status()


@app.post("/admin/runtime-projection")
async def apply_runtime_projection(
    payload: dict[str, Any] = Body(...),
    _: None = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return await _projection_manager().apply(payload)
    except ValueError as exc:
        _projection_manager().last_error = str(exc)[:1000]
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def run() -> None:
    settings = get_settings()
    uvicorn.run("auto_router.main_live:app", host=settings.host, port=settings.port, reload=False)
