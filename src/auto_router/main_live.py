from __future__ import annotations

import uvicorn

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
# provider state. The router fails closed if an enabled provider resolves outside
# loopback/LAN/Tailscale-approved hosts.
enforce_strict_offline_provider_config()

import auto_router.main as main_module
from auto_router.assistx_routes import register_assistx_routes
from auto_router.backlog_routes import register_backlog_routes
from auto_router.cli_routes import register_cli_routes
from auto_router.fleet_routes import router as fleet_router
from auto_router.live_model_routes import register_live_model_routes
from auto_router.main import app, state
from auto_router.memory_routes import register_memory_routes
from auto_router.ops_dashboard_routes import register_ops_dashboard_routes
from auto_router.route_event_patch import install_route_event_patch
from auto_router.service_routes import register_service_routes
from auto_router.settings import get_settings

install_route_event_patch(main_module)
app.state.router_state = state
register_live_model_routes(app, state)
register_service_routes(app, state)
register_cli_routes(app, state)
register_backlog_routes(app, state)
register_ops_dashboard_routes(app, state)
register_assistx_routes(app, state)
register_memory_routes(app, state)
app.include_router(fleet_router)


def run() -> None:
    settings = get_settings()
    uvicorn.run("auto_router.main_live:app", host=settings.host, port=settings.port, reload=False)
