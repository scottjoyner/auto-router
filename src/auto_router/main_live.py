from __future__ import annotations

import uvicorn

import auto_router.main as main_module
from auto_router.cli_routes import register_cli_routes
from auto_router.live_model_routes import register_live_model_routes
from auto_router.main import app, state
from auto_router.route_event_patch import install_route_event_patch
from auto_router.service_routes import register_service_routes
from auto_router.settings import get_settings

install_route_event_patch(main_module)
register_live_model_routes(app, state)
register_service_routes(app, state)
register_cli_routes(app, state)


def run() -> None:
    settings = get_settings()
    uvicorn.run("auto_router.main_live:app", host=settings.host, port=settings.port, reload=False)
