from __future__ import annotations

import uvicorn

from auto_router.live_model_routes import register_live_model_routes
from auto_router.main import app, state
from auto_router.settings import get_settings

register_live_model_routes(app, state)


def run() -> None:
    settings = get_settings()
    uvicorn.run("auto_router.main_live:app", host=settings.host, port=settings.port, reload=False)
