from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from auto_router.preflight import build_preflight_report
from auto_router.gateway import build_agentgateway_status
from auto_router.settings import get_settings


templates = Jinja2Templates(directory="src/auto_router/templates")


def register_ops_dashboard_routes(app: FastAPI, state: Any) -> None:
    @app.get("/api/dashboard/ops-summary", response_class=HTMLResponse)
    async def dashboard_ops_summary(request: Request) -> Any:
        data = build_ops_summary(state, gateway_status=await build_agentgateway_status())
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


def build_ops_summary(state: Any, gateway_status: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = get_settings()
    outbox_summary = state.event_outbox.summary() if hasattr(state, "event_outbox") else {}
    model_registry_summary = state.model_registry.summary() if hasattr(state, "model_registry") else {}
    live_models = state.live_models.snapshot() if hasattr(state, "live_models") else []
    cli_discovery = state.cli_discovery if hasattr(state, "cli_discovery") else []
    cli_summary = _cli_summary(cli_discovery)
    service_summary = _service_summary(state)
    context_model_summary = _context_model_summary(state)
    context_model_lane_summary = _context_model_lane_summary(state)
    context_graph_summary = _context_graph_summary(state)
    provider_tap_summary = state.model_registry.probe_summary() if hasattr(state, "model_registry") else _provider_tap_summary(live_models)
    provider_health_summary = state.model_registry.provider_health_reports() if hasattr(state, "model_registry") else []
    swarm_summary = build_swarm_state_summary(state)
    return {
        "outbox_summary": outbox_summary,
        "model_registry_summary": model_registry_summary,
        "live_models": live_models,
        "provider_tap_summary": provider_tap_summary,
        "provider_health_summary": provider_health_summary,
        "cli_discovery": cli_discovery,
        "cli_summary": cli_summary,
        "service_summary": service_summary,
        "context_model_summary": context_model_summary,
        "context_model_lane_summary": context_model_lane_summary,
        "context_graph_summary": context_graph_summary,
        **swarm_summary,
        "gateway": gateway_status or {},
        "assistx_tasks_configured": bool(settings.assistx_tasks_url),
        "assistx_tasks_url": settings.assistx_tasks_url,
        "assistx_event_sink_configured": bool(settings.assistx_event_sink_url),
        "assistx_event_sink_url": settings.assistx_event_sink_url,
    }


def build_swarm_state_summary(state: Any) -> dict[str, Any]:
    context = getattr(state, "context", None)
    providers = list(context.providers) if context is not None and hasattr(context, "providers") else []
    nodes = list(context.nodes) if context is not None and hasattr(context, "nodes") else []
    services = context.all_services() if context is not None and hasattr(context, "all_services") else []
    models = context.all_models() if context is not None and hasattr(context, "all_models") else []
    provider_health_summary = state.model_registry.provider_health_reports() if hasattr(state, "model_registry") else []
    recent_snapshots = state.model_registry.recent_snapshots(limit=24) if hasattr(state, "model_registry") else []

    health_by_provider = {str(report.get("provider") or ""): report for report in provider_health_summary}
    provider_nodes: dict[str, list[Any]] = defaultdict(list)
    model_nodes: dict[str, list[Any]] = defaultdict(list)
    service_nodes: dict[str, list[Any]] = defaultdict(list)

    for provider in providers:
        if provider.node_id:
            provider_nodes[provider.node_id].append(provider)

    for model in models:
        node_id = model.node_id
        if not node_id and model.provider and context is not None and hasattr(context, "provider_for"):
            provider = context.provider_for(model.provider)
            node_id = provider.node_id if provider is not None else None
        if node_id:
            model_nodes[node_id].append(model)

    for service in services:
        if service.node_id:
            service_nodes[service.node_id].append(service)

    node_map: list[dict[str, Any]] = []
    for node in nodes:
        node_providers = sorted(provider_nodes.get(node.node_id, []), key=lambda item: (item.priority, item.provider))
        node_models = sorted(model_nodes.get(node.node_id, []), key=lambda item: (item.priority, item.provider or "", item.name.lower()))
        node_services = sorted(service_nodes.get(node.node_id, []), key=lambda item: (item.priority, item.name.lower()))
        node_reports = [report for provider in node_providers if (report := health_by_provider.get(provider.provider)) is not None]
        endpoint_health = _endpoint_health_summary(node_services, running=bool(getattr(node, "running", False)))
        recent_history = _node_recent_history(node_providers, health_by_provider)
        node_map.append(
            {
                "node_id": node.node_id,
                "display_name": node.display_name or node.node_id,
                "lane": getattr(node.lane, "value", str(node.lane)),
                "running": bool(node.running),
                "detail": node.detail,
                "provider_count": len(node_providers),
                "model_count": len(node_models),
                "service_count": len(node_services),
                "providers": [provider.provider for provider in node_providers],
                "models": [model.provider_model or model.name for model in node_models[:8]],
                "services": [service.model_dump() for service in node_services[:8]],
                "endpoint_health": endpoint_health,
                "health_scores": [int(report.get("health_score") or 0) for report in node_reports],
                "avg_health_score": int(mean([int(report.get("health_score") or 0) for report in node_reports])) if node_reports else endpoint_health["score"],
                "drift_count": sum(1 for report in node_reports if report and report.get("drift")),
                "recent_history": recent_history,
            }
        )

    provider_health_scores = [int(report.get("health_score") or 0) for report in provider_health_summary if report.get("health_score") is not None]
    provider_model_counts = [int(report.get("model_count") or 0) for report in provider_health_summary]
    provider_latencies = [int(report.get("latency_ms") or 0) for report in provider_health_summary if report.get("latency_ms") is not None]
    endpoint_scores = [item["endpoint_health"]["score"] for item in node_map]
    drift_count = sum(1 for report in provider_health_summary if report.get("drift"))
    return {
        "swarm_memory_map": node_map,
        "swarm_summary": {
            "nodes": len(node_map),
            "providers": len(providers),
            "models": len(models),
            "services": len(services),
            "avg_provider_health_score": int(mean(provider_health_scores)) if provider_health_scores else 0,
            "avg_provider_model_count": round(mean(provider_model_counts), 1) if provider_model_counts else 0,
            "avg_provider_latency_ms": int(mean(provider_latencies)) if provider_latencies else 0,
            "avg_endpoint_health_score": int(mean(endpoint_scores)) if endpoint_scores else 0,
            "drift_providers": drift_count,
            "drift_rate": round(drift_count / len(provider_health_summary), 3) if provider_health_summary else 0,
        },
        "swarm_recent_probes": _flatten_recent_history(provider_health_summary),
        "swarm_recent_snapshots": recent_snapshots,
    }


def _endpoint_health_summary(services: list[Any], running: bool = False) -> dict[str, Any]:
    counts = {"online": 0, "degraded": 0, "offline": 0, "unknown": 0, "blocked": 0}
    scores: list[int] = []
    for service in services:
        status = str(getattr(service.status, "value", service.status))
        counts[status] = counts.get(status, 0) + 1
        scores.append(_status_score(status))
    if scores:
        score = int(mean(scores))
    else:
        score = 100 if running else 0
    if score >= 80:
        state = "healthy"
    elif score >= 50:
        state = "mixed"
    elif score > 0:
        state = "degraded"
    else:
        state = "down"
    return {"score": score, "state": state, "total": len(services), **counts}


def _status_score(status: str) -> int:
    mapping = {"online": 100, "degraded": 65, "unknown": 40, "offline": 0, "blocked": 0}
    return mapping.get(status, 25)


def _node_recent_history(providers: list[Any], health_by_provider: dict[str, dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = int(time.time())
    for provider in providers:
        report = health_by_provider.get(provider.provider)
        if report is None:
            continue
        for item in report.get("recent", [])[:3]:
            fetched_at = int(item.get("fetched_at") or 0)
            rows.append(
                {
                    "provider": provider.provider,
                    "fetched_at": fetched_at,
                    "age_seconds": max(now - fetched_at, 0),
                    "latency_ms": item.get("latency_ms"),
                    "model_count": int(item.get("model_count") or 0),
                    "drift": bool(item.get("drift")),
                    "ok": bool(item.get("ok")),
                }
            )
    return sorted(rows, key=lambda item: (int(item["fetched_at"]), item["provider"]), reverse=True)[:limit]


def _flatten_recent_history(provider_health_summary: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = int(time.time())
    for report in provider_health_summary:
        provider = str(report.get("provider") or "")
        for item in report.get("recent", [])[:3]:
            fetched_at = int(item.get("fetched_at") or 0)
            rows.append(
                {
                    "provider": provider,
                    "fetched_at": fetched_at,
                    "age_seconds": max(now - fetched_at, 0),
                    "latency_ms": item.get("latency_ms"),
                    "model_count": int(item.get("model_count") or 0),
                    "drift": bool(item.get("drift")),
                    "ok": bool(item.get("ok")),
                    "error": item.get("error"),
                }
            )
    return sorted(rows, key=lambda item: (int(item["fetched_at"]), item["provider"]), reverse=True)[:limit]


def render_ops_metrics(summary: dict[str, Any]) -> str:
    outbox = summary.get("outbox_summary") or {}
    models = summary.get("model_registry_summary") or {}
    cli = summary.get("cli_summary") or {}
    services = summary.get("service_summary") or {}
    context_models = summary.get("context_model_summary") or {}
    context_graph = summary.get("context_graph_summary") or {}
    context_model_lanes = summary.get("context_model_lane_summary") or {}
    provider_taps = summary.get("provider_tap_summary") or {}
    provider_health = summary.get("provider_health_summary") or []
    swarm_summary = summary.get("swarm_summary") or {}
    swarm_memory_map = summary.get("swarm_memory_map") or []
    swarm_recent_probes = summary.get("swarm_recent_probes") or []

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
            "# HELP auto_router_context_models Number of context model elements in the swarm snapshot.",
            "# TYPE auto_router_context_models gauge",
            f"auto_router_context_models {int(context_models.get('total') or 0)}",
            "# HELP auto_router_context_model_local Number of local context models in the swarm snapshot.",
            "# TYPE auto_router_context_model_local gauge",
            f"auto_router_context_model_local {int(context_model_lanes.get('local') or context_models.get('local') or 0)}",
            "# HELP auto_router_context_model_free_api Number of free API context models in the swarm snapshot.",
            "# TYPE auto_router_context_model_free_api gauge",
            f"auto_router_context_model_free_api {int(context_model_lanes.get('free_api') or context_models.get('free_api') or 0)}",
            "# HELP auto_router_context_model_blocked Number of blocked context models in the swarm snapshot.",
            "# TYPE auto_router_context_model_blocked gauge",
            f"auto_router_context_model_blocked {int(context_model_lanes.get('blocked') or context_models.get('blocked') or 0)}",
            "# HELP auto_router_context_graph_objects Number of graph objects projected from context.",
            "# TYPE auto_router_context_graph_objects gauge",
            f"auto_router_context_graph_objects {int(context_graph.get('total') or 0)}",
            "# HELP auto_router_provider_taps Number of live provider taps captured in the latest probe.",
            "# TYPE auto_router_provider_taps gauge",
            f"auto_router_provider_taps {int(provider_taps.get('providers') or 0)}",
            "# HELP auto_router_provider_tap_models Number of model records returned across the latest probe.",
            "# TYPE auto_router_provider_tap_models gauge",
            f"auto_router_provider_tap_models {int(provider_taps.get('models') or 0)}",
            "# HELP auto_router_provider_probe_drift Number of providers with drift in the latest probe.",
            "# TYPE auto_router_provider_probe_drift gauge",
            f"auto_router_provider_probe_drift {int(provider_taps.get('drift') or 0)}",
            "# HELP auto_router_provider_probe_ok Number of providers healthy in the latest probe.",
            "# TYPE auto_router_provider_probe_ok gauge",
            f"auto_router_provider_probe_ok {int(provider_taps.get('ok') or 0)}",
            "# HELP auto_router_provider_probe_error Number of providers failing in the latest probe.",
            "# TYPE auto_router_provider_probe_error gauge",
            f"auto_router_provider_probe_error {int(provider_taps.get('error') or 0)}",
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
    for report in provider_health:
        provider = str(report.get("provider") or "")
        lines.append(
            'auto_router_provider_health_score{provider="%s"} %s'
            % (provider, int(report.get("health_score") or 0))
        )
        lines.append(
            'auto_router_provider_probe_age_seconds{provider="%s"} %s'
            % (provider, int(report.get("age_seconds") or 0))
        )
        lines.append(
            'auto_router_provider_probe_drift_latest{provider="%s"} %s'
            % (provider, 1 if report.get("drift") else 0)
        )
    lines.extend(
        [
            "# HELP auto_router_swarm_nodes Number of nodes in the unified swarm memory map.",
            "# TYPE auto_router_swarm_nodes gauge",
            f"auto_router_swarm_nodes {int(swarm_summary.get('nodes') or len(swarm_memory_map))}",
            "# HELP auto_router_swarm_models Number of models projected into the unified swarm memory map.",
            "# TYPE auto_router_swarm_models gauge",
            f"auto_router_swarm_models {int(swarm_summary.get('models') or 0)}",
            "# HELP auto_router_swarm_services Number of services projected into the unified swarm memory map.",
            "# TYPE auto_router_swarm_services gauge",
            f"auto_router_swarm_services {int(swarm_summary.get('services') or 0)}",
            "# HELP auto_router_swarm_avg_provider_health Average provider health score across the swarm.",
            "# TYPE auto_router_swarm_avg_provider_health gauge",
            f"auto_router_swarm_avg_provider_health {int(swarm_summary.get('avg_provider_health_score') or 0)}",
            "# HELP auto_router_swarm_avg_endpoint_health Average endpoint health score across nodes.",
            "# TYPE auto_router_swarm_avg_endpoint_health gauge",
            f"auto_router_swarm_avg_endpoint_health {int(swarm_summary.get('avg_endpoint_health_score') or 0)}",
            "# HELP auto_router_swarm_drift_providers Number of providers with drift in the swarm history.",
            "# TYPE auto_router_swarm_drift_providers gauge",
            f"auto_router_swarm_drift_providers {int(swarm_summary.get('drift_providers') or 0)}",
            "# HELP auto_router_swarm_recent_probes Number of recent probe rows surfaced in the swarm history panel.",
            "# TYPE auto_router_swarm_recent_probes gauge",
            f"auto_router_swarm_recent_probes {int(len(swarm_recent_probes))}",
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


def _context_model_summary(state: Any) -> dict[str, int]:
    models = state.context.all_models() if hasattr(state, "context") else []
    summary = {"total": len(models), "local": 0, "free_api": 0, "blocked": 0}
    for model in models:
        if model.is_blocked:
            summary["blocked"] += 1
        elif model.is_local:
            summary["local"] += 1
        elif model.is_free_api:
            summary["free_api"] += 1
    summary["local_models"] = summary["local"]
    summary["api_models"] = summary["free_api"]
    summary["blocked_models"] = summary["blocked"]
    return summary


def _context_model_lane_summary(state: Any) -> dict[str, int]:
    models = state.context.all_models() if hasattr(state, "context") else []
    return {
        "total": len(models),
        "local": len(state.context.local_models()) if hasattr(state, "context") and hasattr(state.context, "local_models") else 0,
        "free_api": len(state.context.free_api_models()) if hasattr(state, "context") and hasattr(state.context, "free_api_models") else 0,
        "blocked": len(state.context.blocked_models()) if hasattr(state, "context") and hasattr(state.context, "blocked_models") else 0,
    }


def _context_graph_summary(state: Any) -> dict[str, int]:
    if not hasattr(state, "context"):
        return {"total": 0, "node": 0, "provider": 0, "model": 0, "service": 0}
    if hasattr(state.context, "graph_object_summary"):
        return state.context.graph_object_summary()
    return {"total": 0, "node": 0, "provider": 0, "model": 0, "service": 0}


def _provider_tap_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "providers": len(records),
        "ok": sum(1 for record in records if record.get("ok")),
        "error": sum(1 for record in records if not record.get("ok")),
        "models": sum(int(record.get("model_count") or 0) for record in records),
    }


def _cli_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(items),
        "installed": sum(1 for item in items if item.get("installed")),
        "runnable": sum(1 for item in items if item.get("runnable")),
        "missing": sum(1 for item in items if not item.get("installed")),
    }
