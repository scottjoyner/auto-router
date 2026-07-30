# ruff: noqa: E402
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import Body, Depends, FastAPI, HTTPException

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
    # The lease retains its exact gate object. If AssistX publishes a newer
    # generation while this request is running, release still targets the old gate.
    lease = await _admission_controller().acquire(candidate)
    try:
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
    lease = await _admission_controller().acquire(candidate)
    try:
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


@asynccontextmanager
async def strict_offline_lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start only AssistX projection, admission control, and local housekeeping loops.

    The bootstrap YAML remains a fail-closed starting generation. AssistX may then
    publish signed, monotonic runtime projections. Each valid projection is prepared
    fully and atomically swaps provider registry, context, policy engine, admission,
    and access-path selection. Active leases retain their previous gate objects.
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
            else {"configured": False}
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
