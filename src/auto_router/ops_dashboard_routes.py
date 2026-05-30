from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from auto_router.preflight import build_preflight_report
from auto_router.settings import get_settings


templates = Jinja2Templates(directory="src/auto_router/templates")


def register_ops_dashboard_routes(app: FastAPI, state: Any) -> None:
    @app.get("/api/dashboard/ops-summary", response_class=HTMLResponse)
    async def dashboard_ops_summary(request: Request) -> Any:
        data = build_ops_summary(state)
        return templates.TemplateResponse(
            request=request,
            name="fragments/ops_summary.html",
            context=data,
        )

    @app.get("/admin/ops/summary")
    async def admin_ops_summary() -> dict[str, Any]:
        return build_ops_summary(state)

    @app.get("/admin/ops/preflight")
    async def admin_ops_preflight() -> dict[str, Any]:
        return build_preflight_report(state, get_settings())

    @app.get("/metrics/ops", response_class=PlainTextResponse)
    async def ops_metrics() -> str:
        return render_ops_metrics(build_ops_summary(state))


def build_ops_summary(state: Any) -> dict[str, Any]:
    settings = get_settings()
    outbox_summary = state.event_outbox.summary() if hasattr(state, "event_outbox") else {}
    model_registry_summary = state.model_registry.summary() if hasattr(state, "model_registry") else {}
    live_models = state.live_models.snapshot() if hasattr(state, "live_models") else []
    cli_discovery = state.cli_discovery if hasattr(state, "cli_discovery") else []
    cli_summary = _cli_summary(cli_discovery)
    service_summary = _service_summary(state)
    return {
        "outbox_summary": outbox_summary,
        "model_registry_summary": model_registry_summary,
        "live_models": live_models,
        "cli_discovery": cli_discovery,
        "cli_summary": cli_summary,
        "service_summary": service_summary,
        "assistx_tasks_configured": bool(settings.assistx_tasks_url),
        "assistx_tasks_url": settings.assistx_tasks_url,
        "assistx_event_sink_configured": bool(settings.assistx_event_sink_url),
        "assistx_event_sink_url": settings.assistx_event_sink_url,
    }


def render_ops_metrics(summary: dict[str, Any]) -> str:
    outbox = summary.get("outbox_summary") or {}
    models = summary.get("model_registry_summary") or {}
    cli = summary.get("cli_summary") or {}
    services = summary.get("service_summary") or {}
    lines = [
        "# HELP auto_router_outbox_events Number of outbox events by state.",
        "# TYPE auto_router_outbox_events gauge",
    ]
    for state_name in ("pending", "retry", "delivered", "dead_letter"):
        lines.append(f'auto_router_outbox_events{{state="{state_name}"}} {int(outbox.get(state_name) or 0)}')
    lines.extend(
        [
            "# HELP auto_router_model_registry_providers Number of providers in durable model registry.",
            "# TYPE auto_router_model_registry_providers gauge",
            f"auto_router_model_registry_providers {int(models.get('providers') or 0)}",
            "# HELP auto_router_model_registry_models Number of models in latest durable model registry snapshots.",
            "# TYPE auto_router_model_registry_models gauge",
            f"auto_router_model_registry_models {int(models.get('models') or 0)}",
            "# HELP auto_router_model_registry_stale Number of stale provider model registry snapshots.",
            "# TYPE auto_router_model_registry_stale gauge",
            f"auto_router_model_registry_stale {int(models.get('stale') or 0)}",
            "# HELP auto_router_agent_cli_tools Number of discovered agent CLI tools by state.",
            "# TYPE auto_router_agent_cli_tools gauge",
        ]
    )
    for state_name in ("total", "installed", "runnable", "missing"):
        lines.append(f'auto_router_agent_cli_tools{{state="{state_name}"}} {int(cli.get(state_name) or 0)}')
    lines.extend(
        [
            "# HELP auto_router_services Number of registered services by status.",
            "# TYPE auto_router_services gauge",
        ]
    )
    for status_name in ("total", "online", "offline", "degraded", "unknown", "blocked"):
        lines.append(f'auto_router_services{{status="{status_name}"}} {int(services.get(status_name) or 0)}')
    lines.extend(
        [
            "# HELP auto_router_assistx_tasks_configured Whether AssistX backlog task intake is configured.",
            "# TYPE auto_router_assistx_tasks_configured gauge",
            f"auto_router_assistx_tasks_configured {1 if summary.get('assistx_tasks_configured') else 0}",
            "# HELP auto_router_assistx_event_sink_configured Whether AssistX event sink dispatch is configured.",
            "# TYPE auto_router_assistx_event_sink_configured gauge",
            f"auto_router_assistx_event_sink_configured {1 if summary.get('assistx_event_sink_configured') else 0}",
        ]
    )
    return "\n".join(lines) + "\n"


def _service_summary(state: Any) -> dict[str, int]:
    statuses = {"online": 0, "offline": 0, "degraded": 0, "unknown": 0, "blocked": 0}
    services = state.context.all_services() if hasattr(state, "context") else []
    for service in services:
        status = getattr(service.status, "value", str(service.status))
        statuses[status] = statuses.get(status, 0) + 1
    statuses["total"] = len(services)
    return statuses


def _cli_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(items),
        "installed": sum(1 for item in items if item.get("installed")),
        "runnable": sum(1 for item in items if item.get("runnable")),
        "missing": sum(1 for item in items if not item.get("installed")),
    }
