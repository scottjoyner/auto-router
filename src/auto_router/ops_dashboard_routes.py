from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from auto_router.settings import get_settings


templates = Jinja2Templates(directory="src/auto_router/templates")


def register_ops_dashboard_routes(app: FastAPI, state: Any) -> None:
    @app.get("/api/dashboard/ops-summary", response_class=HTMLResponse)
    async def dashboard_ops_summary(request: Request) -> Any:
        settings = get_settings()
        outbox_summary = state.event_outbox.summary() if hasattr(state, "event_outbox") else {}
        model_registry_summary = state.model_registry.summary() if hasattr(state, "model_registry") else {}
        live_models = state.live_models.snapshot() if hasattr(state, "live_models") else []
        cli_discovery = state.cli_discovery if hasattr(state, "cli_discovery") else []
        cli_summary = _cli_summary(cli_discovery)
        return templates.TemplateResponse(
            request=request,
            name="fragments/ops_summary.html",
            context={
                "outbox_summary": outbox_summary,
                "model_registry_summary": model_registry_summary,
                "live_models": live_models,
                "cli_discovery": cli_discovery,
                "cli_summary": cli_summary,
                "assistx_tasks_configured": bool(settings.assistx_tasks_url),
                "assistx_tasks_url": settings.assistx_tasks_url,
                "assistx_event_sink_configured": bool(settings.assistx_event_sink_url),
                "assistx_event_sink_url": settings.assistx_event_sink_url,
            },
        )


def _cli_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(items),
        "installed": sum(1 for item in items if item.get("installed")),
        "runnable": sum(1 for item in items if item.get("runnable")),
        "missing": sum(1 for item in items if not item.get("installed")),
    }
