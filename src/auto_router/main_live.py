from __future__ import annotations

from auto_router.main import app, state
from auto_router.live_model_routes import register_live_model_routes

register_live_model_routes(app, state)
