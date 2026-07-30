# ruff: noqa: E402
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

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

import auto_router.main as main_module
from auto_router.assistx_routes import register_assistx_routes
from auto_router.fleet_routes import router as fleet_router
from auto_router.main import app, state
from auto_router.memory_routes import register_memory_routes
from auto_router.ops_dashboard_routes import register_ops_dashboard_routes
from auto_router.otel import init_otel
from auto_router.route_event_patch import install_route_event_patch
from auto_router.settings import get_settings


async def _cancel_tasks(tasks: list[asyncio.Task[object]]) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


@asynccontextmanager
async def strict_offline_lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start only AssistX projection and local housekeeping loops.

    The reconciled router does not start Tailnet/service/CLI discovery, live-provider
    registry mutation, model placement, fleet execution, or autonomous backlog
    scheduling. AssistX owns those decisions and supplies canonical context.
    """

    await main_module.load_state()
    init_otel()
    tasks: list[asyncio.Task[object]] = [
        asyncio.create_task(main_module.refresh_context_task()),
        asyncio.create_task(main_module.outbox_dispatch_task()),
        asyncio.create_task(main_module.prune_task()),
        asyncio.create_task(main_module.persist_latency_task()),
    ]
    try:
        yield
    finally:
        await _cancel_tasks(tasks)
        try:
            if hasattr(state, "policy_engine"):
                await asyncio.to_thread(state.policy_engine.persist_latency, True)
        except Exception:
            pass


install_route_event_patch(main_module)
app.router.lifespan_context = strict_offline_lifespan
app.state.router_state = state

# Register only operator visibility and AssistX-owned integration surfaces. Legacy
# live-model/service/CLI discovery, backlog scheduling, and in-process agent routes
# remain in history but are not mounted by the reconciled runtime entrypoint.
register_ops_dashboard_routes(app, state)
register_assistx_routes(app, state)
register_memory_routes(app, state)
app.include_router(fleet_router)


def run() -> None:
    settings = get_settings()
    uvicorn.run("auto_router.main_live:app", host=settings.host, port=settings.port, reload=False)
