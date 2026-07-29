from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from auto_router.preflight import build_preflight_report
from auto_router.gateway import build_agentgateway_status
from auto_router.service_routes import build_outbox_dispatch_status, build_outbox_pressure_status
from auto_router.settings import get_settings
from auto_router.ui_pages import get_ui_page_sections


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
    outbox_dispatch_summary = build_outbox_dispatch_status(state)
    outbox_pressure_summary = build_outbox_pressure_status(state)
    model_registry_summary = state.model_registry.summary() if hasattr(state, "model_registry") else {}
    live_models = state.live_models.snapshot() if hasattr(state, "live_models") else []
    circuits = state.circuits.snapshot() if hasattr(state, "circuits") else []
    runtime_summary = state.ledger.runtime_summary() if hasattr(state, "ledger") and hasattr(state.ledger, "runtime_summary") else {}
    recent_runtime_samples = state.ledger.recent_runtime_samples(limit=12) if hasattr(state, "ledger") and hasattr(state.ledger, "recent_runtime_samples") else []
    cli_discovery = state.cli_discovery if hasattr(state, "cli_discovery") else []
    cli_summary = _cli_summary(cli_discovery)
    service_summary = _service_summary(state)
    context_model_summary = _context_model_summary(state)
    context_model_lane_summary = _context_model_lane_summary(state)
    context_graph_summary = _context_graph_summary(state)
    context_signal_summary = _context_signal_summary(state)
    context_route_signal_summary = _context_route_signal_summary(state)
    provider_probe_summary = state.model_registry.probe_summary() if hasattr(state, "model_registry") else {}
    provider_tap_summary = provider_probe_summary or (_provider_tap_summary(live_models))
    provider_health_summary = state.model_registry.provider_health_reports() if hasattr(state, "model_registry") else []
    swarm_summary = build_swarm_state_summary(state)
    context_projection = swarm_summary.get("context_projection", {})
    fleet_dispatcher_stats = _fleet_dispatcher_stats()
    workflow_contract_summary = _workflow_contract_summary(fleet_dispatcher_stats)
    workflow_contract_alert = _workflow_contract_alert_summary(workflow_contract_summary, fleet_dispatcher_stats)
    circuit_breaker_summary = _circuit_breaker_summary(circuits)
    dead_letter_summary = _dead_letter_summary(state, outbox_summary)
    live_model_freshness = _live_model_freshness_summary(live_models, provider_health_summary)
    memory_summary = state.memory_store.summary() if hasattr(state, "memory_store") else {}
    memory_runtime = state.memory_client.metrics() if hasattr(state, "memory_client") else {}
    return {
        "outbox_summary": outbox_summary,
        "outbox_dispatch_summary": outbox_dispatch_summary,
        "outbox_pressure_summary": outbox_pressure_summary,
        "model_registry_summary": model_registry_summary,
        "live_models": live_models,
        "circuits": circuits,
        "circuit_breaker_summary": circuit_breaker_summary,
        "dead_letter_summary": dead_letter_summary,
        "dead_letter_events": dead_letter_summary.get("events", []),
        "dead_letter_reasons": dead_letter_summary.get("top_reasons", []),
        "live_model_freshness": live_model_freshness,
        "memory_summary": memory_summary,
        "memory_runtime": memory_runtime,
        "provider_probe_summary": provider_probe_summary,
        "provider_tap_summary": provider_tap_summary,
        "provider_health_summary": provider_health_summary,
        "runtime_summary": runtime_summary,
        "recent_runtime_samples": recent_runtime_samples,
        "cli_discovery": cli_discovery,
        "cli_summary": cli_summary,
        "service_summary": service_summary,
        "context_model_summary": context_model_summary,
        "context_model_lane_summary": context_model_lane_summary,
        "context_graph_summary": context_graph_summary,
        "context_signal_summary": context_signal_summary,
        "context_route_signal_summary": context_route_signal_summary,
        "context_projection": context_projection,
        **swarm_summary,
        "swarm_runtime_throughput": swarm_summary.get("swarm_runtime_throughput", {}),
        "gateway": gateway_status or {},
        "assistx_tasks_url": settings.assistx_tasks_url,
        "assistx_event_sink_url": settings.assistx_event_sink_url,
        "assistx_tasks_configured": bool(settings.assistx_tasks_url),
        "assistx_event_sink_configured": bool(settings.assistx_event_sink_url),
        "ui_page_sections": get_ui_page_sections(),
        "fleet_dispatcher_stats": fleet_dispatcher_stats,
        "fleet_loadout_report": _fleet_loadout_report(),
        "workflow_contract_summary": workflow_contract_summary,
        "workflow_contract_alert": workflow_contract_alert,
    }


FLEET_DISPATCHER_STATS_PATH = Path(os.getenv("AUTO_ROUTER_FLEET_DISPATCHER_STATS_PATH", "/data/fleet_dispatcher_stats.json"))
FLEET_LOADOUT_REPORT_PATH = Path(os.getenv("AUTO_ROUTER_FLEET_LOADOUT_REPORT_PATH", "/data/fleet_loadout_report.json"))


def _fleet_dispatcher_stats() -> dict[str, Any]:
    if not FLEET_DISPATCHER_STATS_PATH.exists():
        return {}
    try:
        return json.loads(FLEET_DISPATCHER_STATS_PATH.read_text())
    except Exception:
        return {}


def _fleet_loadout_report() -> dict[str, Any]:
    if not FLEET_LOADOUT_REPORT_PATH.exists():
        return {}
    try:
        return json.loads(FLEET_LOADOUT_REPORT_PATH.read_text())
    except Exception:
        return {}


def _workflow_contract_summary(fleet_dispatcher_stats: dict[str, Any]) -> dict[str, Any]:
    fleet_stats = fleet_dispatcher_stats.get("stats") or {}
    queues = fleet_dispatcher_stats.get("queues") or {}
    stage_counts = {str(stage): int(count or 0) for stage, count in (fleet_stats.get("by_stage") or {}).items()}
    recent_snapshots = list(fleet_stats.get("recent_snapshots") or [])
    handoff_snapshot_count = sum(
        1
        for snapshot in recent_snapshots
        if int(((snapshot.get("stats") or {}).get("by_stage") or {}).get("handoff") or 0) > 0
    )
    plan_steps = [
        "Inspect the current state one slice at a time.",
        "Make the smallest safe change or conclusion.",
        "Validate the result against the acceptance criteria.",
        "Report risks, gaps, and handoff notes.",
    ]
    validation_metrics = ["acceptance_criteria_met", "regressions_checked", "handoff_ready"]
    review_checkpoints = ["reviewed by local iteration", "validated against plan", "final handoff approved"]

    changeover_gaps: list[dict[str, Any]] = []
    if not fleet_dispatcher_stats:
        changeover_gaps.append(
            {
                "title": "No live dispatcher snapshot yet",
                "detail": "The live fleet consumer has not written its stats payload, so changeover coverage is still inferred from code paths rather than runtime evidence.",
            }
        )
    if stage_counts and stage_counts.get("handoff", 0) == 0:
        changeover_gaps.append(
            {
                "title": "No observed handoff-stage traffic",
                "detail": "The dispatcher snapshot shows work and review traffic, but no completed handoff-stage work yet, so the final-review path is not proven in production traffic.",
            }
        )
    elif stage_counts.get("handoff", 0) > 0 and handoff_snapshot_count < 2:
        changeover_gaps.append(
            {
                "title": "Handoff traffic needs a second live snapshot",
                "detail": f"The live stage counts have handoff traffic, but only {handoff_snapshot_count} snapshot(s) so far show it, so the proof still needs one more production sample.",
            }
        )
    if int(queues.get("review") or 0) > 0:
        changeover_gaps.append(
            {
                "title": "Review backlog is still non-zero",
                "detail": f"The live queue still has {int(queues.get('review') or 0)} review items waiting, so the new reviewer/handoff path is not yet caught up.",
            }
        )
    if int(fleet_stats.get("completed") or 0) == 0:
        changeover_gaps.append(
            {
                "title": "No completed tasks in the new snapshot",
                "detail": "We can see the contract fields and queue geometry, but the rollout still needs real completed jobs to validate the updated workflow end-to-end.",
            }
        )
    if not stage_counts:
        changeover_gaps.append(
            {
                "title": "Stage breakdown still absent",
                "detail": "The metrics file is not yet exposing per-stage counts, so the dashboard cannot show whether work, review, and handoff are balancing correctly.",
            }
        )

    return {
        "profile_name": "iterative_review_handoff",
        "workflow_stage": "handoff",
        "plan_steps": plan_steps,
        "validation_metrics": validation_metrics,
        "review_checkpoints": review_checkpoints,
        "stage_counts": stage_counts,
        "handoff_snapshot_count": handoff_snapshot_count,
        "handoff_confirmed": handoff_snapshot_count >= 2,
        "completed_tasks": int(fleet_stats.get("completed") or 0),
        "changeover_gaps": changeover_gaps,
    }


def _workflow_contract_alert_summary(workflow_contract: dict[str, Any], fleet_dispatcher_stats: dict[str, Any]) -> dict[str, Any]:
    stage_counts = workflow_contract.get("stage_counts") or {}
    changeover_gaps = workflow_contract.get("changeover_gaps") or []
    fleet_queues = fleet_dispatcher_stats.get("queues") or {}
    review_queue_depth = int(fleet_queues.get("review") or 0)
    worker_queue_depth = int(fleet_queues.get("worker") or 0)
    handoff_present = int(stage_counts.get("handoff") or 0) > 0
    handoff_snapshot_count = int(workflow_contract.get("handoff_snapshot_count") or 0)
    handoff_confirmed = bool(workflow_contract.get("handoff_confirmed") or False)
    completed_tasks = int(workflow_contract.get("completed_tasks") or 0)

    if not fleet_dispatcher_stats:
        return {
            "active": True,
            "level": "critical",
            "headline": "No live dispatcher snapshot yet",
            "detail": "We cannot confirm the new workflow path until the live dispatcher writes a fresh snapshot.",
            "action": "Start or recover the dispatcher, then re-check the rollout snapshot.",
            "open_gaps": len(changeover_gaps),
            "handoff_present": False,
            "handoff_snapshot_count": handoff_snapshot_count,
            "handoff_confirmed": handoff_confirmed,
            "review_queue_depth": review_queue_depth,
            "worker_queue_depth": worker_queue_depth,
            "completed_tasks": completed_tasks,
        }

    if not handoff_present:
        return {
            "active": True,
            "level": "warning",
            "headline": "Handoff-stage traffic has not shown up yet",
            "detail": "The live snapshot still shows work and review activity, but no completed handoff-stage traffic to prove the final leg is flowing.",
            "action": "Keep routing a few end-to-end jobs through the new path until handoff appears in the live stage counts.",
            "open_gaps": len(changeover_gaps),
            "handoff_present": False,
            "handoff_snapshot_count": handoff_snapshot_count,
            "handoff_confirmed": handoff_confirmed,
            "review_queue_depth": review_queue_depth,
            "worker_queue_depth": worker_queue_depth,
            "completed_tasks": completed_tasks,
        }

    if not handoff_confirmed:
        return {
            "active": True,
            "level": "watch",
            "headline": "Handoff traffic still needs one more snapshot",
            "detail": f"The live stage counts already include handoff traffic, but only {handoff_snapshot_count} production snapshot(s) have captured it so far.",
            "action": "Let the dispatcher write one more production snapshot with handoff traffic before closing the proof.",
            "open_gaps": len(changeover_gaps),
            "handoff_present": True,
            "handoff_snapshot_count": handoff_snapshot_count,
            "handoff_confirmed": False,
            "review_queue_depth": review_queue_depth,
            "worker_queue_depth": worker_queue_depth,
            "completed_tasks": completed_tasks,
        }

    if review_queue_depth > 0:
        return {
            "active": True,
            "level": "warning",
            "headline": "Review backlog is still burning down",
            "detail": f"The dispatcher still has {review_queue_depth} review items waiting, so the rollout is active but not yet caught up.",
            "action": "Let the reviewer lane clear and confirm the backlog trends downward on the next snapshot.",
            "open_gaps": len(changeover_gaps),
            "handoff_present": True,
            "handoff_snapshot_count": handoff_snapshot_count,
            "handoff_confirmed": True,
            "review_queue_depth": review_queue_depth,
            "worker_queue_depth": worker_queue_depth,
            "completed_tasks": completed_tasks,
        }

    if changeover_gaps:
        return {
            "active": True,
            "level": "watch",
            "headline": "Changeover gaps still need proof",
            "detail": f"There are still {len(changeover_gaps)} rollout gaps open, even though the handoff lane is now visible.",
            "action": "Close the remaining evidence gaps and keep the stage counts moving toward steady state.",
            "open_gaps": len(changeover_gaps),
            "handoff_present": True,
            "handoff_snapshot_count": handoff_snapshot_count,
            "handoff_confirmed": True,
            "review_queue_depth": review_queue_depth,
            "worker_queue_depth": worker_queue_depth,
            "completed_tasks": completed_tasks,
        }

    return {
        "active": False,
        "level": "ok",
        "headline": "Rollout is caught up",
        "detail": "The live snapshot shows handoff traffic, no review backlog, and no remaining changeover gaps.",
        "action": "Keep the dashboard on watch, but no immediate intervention is required.",
        "open_gaps": 0,
        "handoff_present": True,
        "handoff_snapshot_count": handoff_snapshot_count,
        "handoff_confirmed": True,
        "review_queue_depth": review_queue_depth,
        "worker_queue_depth": worker_queue_depth,
        "completed_tasks": completed_tasks,
    }


def _circuit_breaker_summary(circuits: list[dict[str, Any]]) -> dict[str, Any]:
    open_circuits = [c for c in circuits if c.get("open")]
    closed_circuits = [c for c in circuits if not c.get("open")]
    sorted_circuits = sorted(circuits, key=lambda item: (not bool(item.get("open")), str(item.get("owner") or "")))
    return {
        "total": len(circuits),
        "open": len(open_circuits),
        "closed": len(closed_circuits),
        "circuits": sorted_circuits,
    }


def _dead_letter_summary(state: Any, outbox_summary: dict[str, Any]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    if hasattr(state, "event_outbox") and hasattr(state.event_outbox, "recent"):
        try:
            recent = state.event_outbox.recent(limit=24)
        except Exception:
            recent = []
        events = [event for event in recent if str(event.get("status") or "").lower() in {"retry", "dead_letter"}]
    reason_counts: dict[str, int] = defaultdict(int)
    for event in events:
        reason = str(event.get("last_error") or "unknown").strip() or "unknown"
        reason_counts[reason] += 1
    top_reasons = [
        {"reason": reason, "count": count}
        for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]
    return {
        "pending": int(outbox_summary.get("pending") or 0),
        "retry": int(outbox_summary.get("retry") or 0),
        "delivered": int(outbox_summary.get("delivered") or 0),
        "dead_letter": int(outbox_summary.get("dead_letter") or 0),
        "events": events[:8],
        "top_reasons": top_reasons,
    }


def _live_model_freshness_summary(live_models: list[dict[str, Any]], provider_health_summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = int(time.time())
    health_by_provider = {str(report.get("provider") or "").strip().lower(): report for report in provider_health_summary}
    rows: list[dict[str, Any]] = []
    for snapshot in live_models:
        provider = str(snapshot.get("provider") or snapshot.get("provider_id") or "").strip().lower()
        health = health_by_provider.get(provider, {})
        fetched_at = snapshot.get("fetched_at")
        age_seconds = max(now - int(fetched_at), 0) if fetched_at else None
        rows.append({
            "provider": provider,
            "ok": bool(snapshot.get("ok")),
            "stale": bool(snapshot.get("stale")),
            "model_count": int(snapshot.get("model_count") or 0),
            "latency_ms": snapshot.get("latency_ms"),
            "age_seconds": age_seconds if age_seconds is not None else health.get("age_seconds"),
            "error": snapshot.get("error") or health.get("error"),
            "signature": snapshot.get("signature"),
            "previous_signature": snapshot.get("previous_signature"),
        })
    return sorted(rows, key=lambda item: (not item["stale"], item["age_seconds"] is None, -(item["age_seconds"] or 0), item["provider"]))




def build_swarm_state_summary(state: Any, provider_health_summary: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    context = getattr(state, "context", None)
    providers = list(context.providers) if context is not None and hasattr(context, "providers") else []
    nodes = list(context.nodes) if context is not None and hasattr(context, "nodes") else []
    services = context.all_services() if context is not None and hasattr(context, "all_services") else []
    models = context.all_models() if context is not None and hasattr(context, "all_models") else []
    signals = context.all_signals() if context is not None and hasattr(context, "all_signals") else []
    resolved_provider_health_summary: list[dict[str, Any]] = provider_health_summary if provider_health_summary is not None else (state.model_registry.provider_health_reports() if hasattr(state, "model_registry") else [])
    recent_snapshots = state.model_registry.recent_snapshots(limit=24) if hasattr(state, "model_registry") and hasattr(state.model_registry, "recent_snapshots") else []
    recent_runtime_samples = state.ledger.recent_runtime_samples(limit=48) if hasattr(state, "ledger") and hasattr(state.ledger, "recent_runtime_samples") else []

    canonical_provider = getattr(context, "canonical_provider_name", lambda value: str(value).strip().lower()) if context is not None else (lambda value: str(value).strip().lower())
    canonical_model_id = getattr(context, "canonical_model_id", lambda value: str(value).strip().lower()) if context is not None else (lambda value: str(value).strip().lower())
    if context is not None:
        resolved_provider_health_summary = [
            {
                **report,
                "provider": canonical_provider(str(report.get("provider") or "")),
            }
            for report in resolved_provider_health_summary
        ]
    health_by_provider = {str(report.get("provider") or ""): report for report in resolved_provider_health_summary}
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

    runtime_by_provider = _runtime_throughput_by_provider(recent_runtime_samples)
    runtime_by_model = _runtime_throughput_by_model(recent_runtime_samples)

    node_map: list[dict[str, Any]] = []
    for node in nodes:
        node_providers = sorted(provider_nodes.get(node.node_id, []), key=lambda item: (getattr(item, "priority", 100), item.provider))
        node_models = sorted(model_nodes.get(node.node_id, []), key=lambda item: (getattr(item, "priority", 100), item.provider or "", item.name.lower()))
        node_services = sorted(service_nodes.get(node.node_id, []), key=lambda item: (getattr(item, "priority", 100), item.name.lower()))
        node_reports = [report for provider in node_providers if (report := health_by_provider.get(canonical_provider(provider.provider))) is not None]
        endpoint_health = _endpoint_health_summary(node_services, running=bool(getattr(node, "running", False)))
        recent_history = _node_recent_history(node_providers, health_by_provider)
        node_signals = _node_signals(context, node.node_id) if context is not None else []
        node_runtime = _node_runtime_summary(node_providers, runtime_by_provider, runtime_by_model)
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
                "signal_count": len(node_signals),
                "providers": [canonical_provider(provider.provider) for provider in node_providers],
                "models": [canonical_model_id(f"{model.provider}.{model.provider_model or model.name}") for model in node_models[:8]],
                "services": [service.model_dump() for service in node_services[:8]],
                "signals": [signal.model_dump() for signal in node_signals[:8]],
                "endpoint_health": endpoint_health,
                "health_scores": [int(report.get("health_score") or 0) for report in node_reports],
                "avg_health_score": int(mean([int(report.get("health_score") or 0) for report in node_reports])) if node_reports else endpoint_health["score"],
                "drift_count": sum(1 for report in node_reports if report and report.get("drift")),
                "recent_history": recent_history,
                "runtime": node_runtime,
            }
        )

    provider_health_scores = [int(report.get("health_score") or 0) for report in resolved_provider_health_summary if report.get("health_score") is not None]
    provider_model_counts = [int(report.get("model_count") or 0) for report in resolved_provider_health_summary]
    provider_latencies = [int(report.get("latency_ms") or 0) for report in resolved_provider_health_summary if report.get("latency_ms") is not None]
    endpoint_scores = [item["endpoint_health"]["score"] for item in node_map]
    drift_count = sum(1 for report in resolved_provider_health_summary if report.get("drift"))
    signal_counts = len(signals)
    context_projection = {
        "status": getattr(context, "projection_status", lambda: "bootstrap")() if context is not None else "missing",
        "degraded": getattr(context, "is_projection_degraded", lambda: False)() if context is not None else True,
        "error": getattr(context, "projection_error", lambda: "")() if context is not None else "",
        "source": getattr(context, "source", "") if context is not None else "",
        "revision": getattr(context, "revision", "") if context is not None else "",
    }
    return {
        "swarm_memory_map": node_map,
        "swarm_summary": {
            "nodes": len(node_map),
            "providers": len(providers),
            "models": len(models),
            "services": len(services),
            "signals": signal_counts,
            "avg_provider_health_score": int(mean(provider_health_scores)) if provider_health_scores else 0,
            "avg_provider_model_count": round(mean(provider_model_counts), 1) if provider_model_counts else 0,
            "avg_provider_latency_ms": int(mean(provider_latencies)) if provider_latencies else 0,
            "avg_endpoint_health_score": int(mean(endpoint_scores)) if endpoint_scores else 0,
            "drift_providers": drift_count,
            "drift_rate": round(drift_count / len(resolved_provider_health_summary), 3) if resolved_provider_health_summary else 0,
        },
        "context_projection": context_projection,
        "swarm_recent_probes": _flatten_recent_history(resolved_provider_health_summary),
        "swarm_recent_snapshots": recent_snapshots,
        "swarm_runtime_throughput": {
            "by_provider": runtime_by_provider,
            "by_model": runtime_by_model,
        },
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
    canonical_provider = lambda value: str(value).strip().lower()
    for provider in providers:
        provider_id = canonical_provider(provider.provider)
        report = health_by_provider.get(provider_id)
        if report is None:
            continue
        for item in report.get("recent", [])[:3]:
            fetched_at = int(item.get("fetched_at") or 0)
            rows.append(
                {
                    "provider": provider_id,
                    "fetched_at": fetched_at,
                    "age_seconds": max(now - fetched_at, 0),
                    "latency_ms": item.get("latency_ms"),
                    "model_count": int(item.get("model_count") or 0),
                    "drift": bool(item.get("drift")),
                    "ok": bool(item.get("ok")),
                }
            )
    return sorted(rows, key=lambda item: (int(item["fetched_at"]), item["provider"]), reverse=True)[:limit]


def _node_signals(context: Any, node_id: str) -> list[Any]:
    if context is None or not hasattr(context, "signals_for_node"):
        return []
    return context.signals_for_node(node_id)


def _runtime_throughput_by_provider(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        provider = str(sample.get("provider_id") or sample.get("provider") or "").strip().lower()
        if provider:
            buckets[provider].append(sample)
    summary: dict[str, dict[str, Any]] = {}
    for provider, rows in buckets.items():
        summary[provider] = {
            "samples": len(rows),
            "avg_latency_ms": round(mean([float(row.get("elapsed_ms") or row.get("latency_ms") or 0) for row in rows]), 1),
            "avg_queue_wait_ms": round(mean([float(row.get("queue_wait_ms") or 0) for row in rows]), 1),
            "avg_load_time_ms": round(mean([float(row.get("load_time_ms") or 0) for row in rows]), 1),
            "avg_tokens_per_second": round(mean([float(row.get("tokens_per_second") or 0) for row in rows]), 2),
            "avg_value_per_second": round(mean([float(row.get("value_per_second") or 0) for row in rows]), 2),
        }
    return summary


def _runtime_throughput_by_model(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        model = str(sample.get("model_id") or sample.get("model") or "").strip().lower()
        if model:
            buckets[model].append(sample)
    summary: dict[str, dict[str, Any]] = {}
    for model, rows in buckets.items():
        summary[model] = {
            "samples": len(rows),
            "avg_latency_ms": round(mean([float(row.get("elapsed_ms") or row.get("latency_ms") or 0) for row in rows]), 1),
            "avg_queue_wait_ms": round(mean([float(row.get("queue_wait_ms") or 0) for row in rows]), 1),
            "avg_tokens_per_second": round(mean([float(row.get("tokens_per_second") or 0) for row in rows]), 2),
            "avg_value_per_second": round(mean([float(row.get("value_per_second") or 0) for row in rows]), 2),
        }
    return summary


def _node_runtime_summary(
    providers: list[Any],
    runtime_by_provider: dict[str, dict[str, Any]],
    runtime_by_model: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    provider_ids = [str(getattr(provider, "provider", "")).strip().lower() for provider in providers if str(getattr(provider, "provider", "")).strip()]
    provider_rows = [runtime_by_provider[provider] for provider in provider_ids if provider in runtime_by_provider]
    if not provider_rows:
        return {"samples": 0, "avg_tokens_per_second": 0, "avg_value_per_second": 0, "avg_latency_ms": 0, "avg_queue_wait_ms": 0, "models": []}
    related_models = []
    for provider in provider_ids:
        for model_id, row in runtime_by_model.items():
            if model_id.startswith(provider) or provider in model_id:
                related_models.append({"model_id": model_id, **row})
    return {
        "samples": sum(int(row.get("samples") or 0) for row in provider_rows),
        "avg_tokens_per_second": round(mean([float(row.get("avg_tokens_per_second") or 0) for row in provider_rows]), 2),
        "avg_value_per_second": round(mean([float(row.get("avg_value_per_second") or 0) for row in provider_rows]), 2),
        "avg_latency_ms": round(mean([float(row.get("avg_latency_ms") or 0) for row in provider_rows]), 1),
        "avg_queue_wait_ms": round(mean([float(row.get("avg_queue_wait_ms") or 0) for row in provider_rows]), 1),
        "models": sorted(related_models, key=lambda item: (-float(item.get("avg_tokens_per_second") or 0), item.get("model_id", "")))[:6],
    }


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
    outbox_pressure = summary.get("outbox_pressure_summary") or {}
    models = summary.get("model_registry_summary") or {}
    cli = summary.get("cli_summary") or {}
    services = summary.get("service_summary") or {}
    context_models = summary.get("context_model_summary") or {}
    context_graph = summary.get("context_graph_summary") or {}
    context_model_lanes = summary.get("context_model_lane_summary") or {}
    context_signals = summary.get("context_signal_summary") or {}
    context_route_signals = summary.get("context_route_signal_summary") or {}
    provider_taps = summary.get("provider_tap_summary") or {}
    provider_health = summary.get("provider_health_summary") or []
    runtime_summary = summary.get("runtime_summary") or {}
    recent_runtime_samples = summary.get("recent_runtime_samples") or []
    swarm_summary = summary.get("swarm_summary") or {}
    swarm_memory_map = summary.get("swarm_memory_map") or []
    swarm_recent_probes = summary.get("swarm_recent_probes") or []
    swarm_runtime_throughput = summary.get("swarm_runtime_throughput") or {}
    fleet = summary.get("fleet_dispatcher_stats") or {}
    fleet_queues = fleet.get("queues") or {}
    fleet_summary = fleet.get("summary") or {}
    fleet_stats = fleet.get("stats") or {}
    fleet_slots = fleet.get("slots") or []
    workflow_contract = summary.get("workflow_contract_summary") or {}
    workflow_contract_alert = summary.get("workflow_contract_alert") or {}
    memory_summary = summary.get("memory_summary") or {}
    memory_runtime = summary.get("memory_runtime") or {}

    lines = [
        "# HELP auto_router_outbox_events Number of outbox events by state.",
        "# TYPE auto_router_outbox_events gauge",
    ]
    lines.extend(
        [
            "# HELP auto_router_memory_total Number of locally cached memories.",
            "# TYPE auto_router_memory_total gauge",
            f"auto_router_memory_total {int(memory_summary.get('total') or 0)}",
            "# HELP auto_router_memory_outcome_events Recorded memory-assisted outcomes.",
            "# TYPE auto_router_memory_outcome_events counter",
            f"auto_router_memory_outcome_events {int(memory_summary.get('outcome_events') or 0)}",
            "# HELP auto_router_memory_assisted_success_rate Memory-assisted success rate.",
            "# TYPE auto_router_memory_assisted_success_rate gauge",
            "auto_router_memory_assisted_success_rate "
            f"{float(memory_summary.get('memory_assisted_success_rate') or 0)}",
            "# HELP auto_router_memory_retrievals Memory context retrievals.",
            "# TYPE auto_router_memory_retrievals counter",
            f"auto_router_memory_retrievals {int(memory_runtime.get('retrievals') or 0)}",
            "# HELP auto_router_memory_local_fallbacks Locally served memory lookups.",
            "# TYPE auto_router_memory_local_fallbacks counter",
            "auto_router_memory_local_fallbacks "
            f"{int(memory_runtime.get('local_fallbacks') or 0)}",
            "# HELP auto_router_memory_remote_failures Remote memory lookup failures.",
            "# TYPE auto_router_memory_remote_failures counter",
            "auto_router_memory_remote_failures "
            f"{int(memory_runtime.get('remote_failures') or 0)}",
            "# HELP auto_router_memory_avg_retrieval_ms Mean memory retrieval latency.",
            "# TYPE auto_router_memory_avg_retrieval_ms gauge",
            "auto_router_memory_avg_retrieval_ms "
            f"{float(memory_runtime.get('avg_retrieval_ms') or 0)}",
            "# HELP auto_router_memory_context_tokens Estimated injected context tokens.",
            "# TYPE auto_router_memory_context_tokens counter",
            "auto_router_memory_context_tokens "
            f"{int(memory_runtime.get('context_tokens') or 0)}",
        ]
    )
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
            "# HELP auto_router_context_signals Number of durable signal objects projected from context.",
            "# TYPE auto_router_context_signals gauge",
            f"auto_router_context_signals {int(context_signals.get('total') or 0)}",
            "# HELP auto_router_context_active_signals Number of active durable signal objects projected from context.",
            "# TYPE auto_router_context_active_signals gauge",
            f"auto_router_context_active_signals {int(context_signals.get('active') or 0)}",
            "# HELP auto_router_route_signals Number of route decision/execution signals in the active context.",
            "# TYPE auto_router_route_signals gauge",
            f"auto_router_route_signals {int(context_route_signals.get('total') or 0)}",
            "# HELP auto_router_route_signals_active Number of active route decision/execution signals in the active context.",
            "# TYPE auto_router_route_signals_active gauge",
            f"auto_router_route_signals_active {int(context_route_signals.get('active') or 0)}",
            "# HELP auto_router_route_signals_preferred Number of preferred route signals in the active context.",
            "# TYPE auto_router_route_signals_preferred gauge",
            f"auto_router_route_signals_preferred {int(context_route_signals.get('preferred') or 0)}",
            "# HELP auto_router_route_signals_blocked Number of blocked route signals in the active context.",
            "# TYPE auto_router_route_signals_blocked gauge",
            f"auto_router_route_signals_blocked {int(context_route_signals.get('blocked') or 0)}",
            "# HELP auto_router_route_signals_realtime Number of realtime route signals in the active context.",
            "# TYPE auto_router_route_signals_realtime gauge",
            f"auto_router_route_signals_realtime {int(context_route_signals.get('realtime') or 0)}",
            "# HELP auto_router_route_signals_avoid Number of avoid route signals in the active context.",
            "# TYPE auto_router_route_signals_avoid gauge",
            f"auto_router_route_signals_avoid {int(context_route_signals.get('avoid') or 0)}",
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
            "# HELP auto_router_runtime_samples Number of persisted runtime samples.",
            "# TYPE auto_router_runtime_samples gauge",
            f"auto_router_runtime_samples {int(runtime_summary.get('samples') or 0)}",
            "# HELP auto_router_runtime_samples_successful Number of successful runtime samples.",
            "# TYPE auto_router_runtime_samples_successful gauge",
            f"auto_router_runtime_samples_successful {int(runtime_summary.get('successful') or 0)}",
            "# HELP auto_router_runtime_samples_failed Number of failed runtime samples.",
            "# TYPE auto_router_runtime_samples_failed gauge",
            f"auto_router_runtime_samples_failed {int(runtime_summary.get('failed') or 0)}",
            "# HELP auto_router_runtime_avg_latency_ms Average latency across recent runtime samples.",
            "# TYPE auto_router_runtime_avg_latency_ms gauge",
            f"auto_router_runtime_avg_latency_ms {int(runtime_summary.get('avg_latency_ms') or 0)}",
            "# HELP auto_router_runtime_avg_elapsed_ms Average wall-clock elapsed time across recent runtime samples.",
            "# TYPE auto_router_runtime_avg_elapsed_ms gauge",
            f"auto_router_runtime_avg_elapsed_ms {int(runtime_summary.get('avg_elapsed_ms') or 0)}",
            "# HELP auto_router_runtime_avg_queue_wait_ms Average queue wait time across recent runtime samples.",
            "# TYPE auto_router_runtime_avg_queue_wait_ms gauge",
            f"auto_router_runtime_avg_queue_wait_ms {int(runtime_summary.get('avg_queue_wait_ms') or 0)}",
            "# HELP auto_router_runtime_avg_load_time_ms Average load time across recent runtime samples.",
            "# TYPE auto_router_runtime_avg_load_time_ms gauge",
            f"auto_router_runtime_avg_load_time_ms {int(runtime_summary.get('avg_load_time_ms') or 0)}",
            "# HELP auto_router_runtime_avg_tokens_per_second Average token throughput across recent runtime samples.",
            "# TYPE auto_router_runtime_avg_tokens_per_second gauge",
            f"auto_router_runtime_avg_tokens_per_second {float(runtime_summary.get('avg_tokens_per_second') or 0)}",
            "# HELP auto_router_runtime_avg_value_per_second Average value throughput across recent runtime samples.",
            "# TYPE auto_router_runtime_avg_value_per_second gauge",
            f"auto_router_runtime_avg_value_per_second {float(runtime_summary.get('avg_value_per_second') or 0)}",
        ]
    )
    for provider_summary in runtime_summary.get("by_provider", []):
        provider = str(provider_summary.get("provider_id") or provider_summary.get("provider") or "unknown")
        lines.append(
            'auto_router_runtime_provider_samples{provider="%s"} %s'
            % (provider, int(provider_summary.get("samples") or 0))
        )
        lines.append(
            'auto_router_runtime_provider_avg_tokens_per_second{provider="%s"} %s'
            % (provider, float(provider_summary.get("avg_tokens_per_second") or 0))
        )
        lines.append(
            'auto_router_runtime_provider_avg_value_per_second{provider="%s"} %s'
            % (provider, float(provider_summary.get("avg_value_per_second") or 0))
        )
        lines.append(
            'auto_router_runtime_provider_avg_queue_wait_ms{provider="%s"} %s'
            % (provider, int(provider_summary.get("avg_queue_wait_ms") or 0))
        )
        lines.append(
            'auto_router_runtime_provider_avg_load_time_ms{provider="%s"} %s'
            % (provider, int(provider_summary.get("avg_load_time_ms") or 0))
        )
    for model_id, model_summary in (swarm_runtime_throughput.get("by_model") or {}).items():
        lines.append(
            'auto_router_runtime_model_samples{model="%s"} %s'
            % (model_id, int(model_summary.get("samples") or 0))
        )
        lines.append(
            'auto_router_runtime_model_avg_tokens_per_second{model="%s"} %s'
            % (model_id, float(model_summary.get("avg_tokens_per_second") or 0))
        )
        lines.append(
            'auto_router_runtime_model_avg_value_per_second{model="%s"} %s'
            % (model_id, float(model_summary.get("avg_value_per_second") or 0))
        )
        lines.append(
            'auto_router_runtime_model_avg_queue_wait_ms{model="%s"} %s'
            % (model_id, float(model_summary.get("avg_queue_wait_ms") or 0))
        )
        lines.append(
            'auto_router_runtime_model_avg_latency_ms{model="%s"} %s'
            % (model_id, float(model_summary.get("avg_latency_ms") or 0))
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
            "# HELP auto_router_fleet_queue_depth Total queued dispatcher work items across worker and review lanes.",
            "# TYPE auto_router_fleet_queue_depth gauge",
            f"auto_router_fleet_queue_depth {int(fleet_queues.get('review') or 0) + int(fleet_queues.get('worker') or 0)}",
            "# HELP auto_router_fleet_avg_queue_wait_ms Average queue wait time for completed fleet tasks.",
            "# TYPE auto_router_fleet_avg_queue_wait_ms gauge",
            f"auto_router_fleet_avg_queue_wait_ms {float(fleet_summary.get('avg_queue_wait_ms') or 0)}",
            "# HELP auto_router_fleet_avg_dispatch_latency_ms Average dispatch latency for completed fleet tasks.",
            "# TYPE auto_router_fleet_avg_dispatch_latency_ms gauge",
            f"auto_router_fleet_avg_dispatch_latency_ms {float(fleet_summary.get('avg_dispatch_latency_ms') or 0)}",
            "# HELP auto_router_fleet_avg_latency_ms Average model latency for completed fleet tasks.",
            "# TYPE auto_router_fleet_avg_latency_ms gauge",
            f"auto_router_fleet_avg_latency_ms {float(fleet_summary.get('avg_latency_ms') or 0)}",
            "# HELP auto_router_fleet_avg_quality_score Average quality score for completed fleet tasks.",
            "# TYPE auto_router_fleet_avg_quality_score gauge",
            f"auto_router_fleet_avg_quality_score {float(fleet_summary.get('avg_quality_score') or 0)}",
            "# HELP auto_router_fleet_avg_response_chars Average response size for completed fleet tasks.",
            "# TYPE auto_router_fleet_avg_response_chars gauge",
            f"auto_router_fleet_avg_response_chars {float(fleet_summary.get('avg_response_chars') or 0)}",
            "# HELP auto_router_fleet_active_slots Number of active dispatcher slots.",
            "# TYPE auto_router_fleet_active_slots gauge",
            f"auto_router_fleet_active_slots {int(fleet_summary.get('active_slots') or len(fleet_slots))}",
            "# HELP auto_router_fleet_busy_slots Number of currently in-flight dispatcher slots.",
            "# TYPE auto_router_fleet_busy_slots gauge",
            f"auto_router_fleet_busy_slots {int(fleet_summary.get('busy_slots') or sum(1 for slot in fleet_slots if slot.get('in_flight'))) }",
            "# HELP auto_router_fleet_idle_slots Number of currently idle dispatcher slots.",
            "# TYPE auto_router_fleet_idle_slots gauge",
            f"auto_router_fleet_idle_slots {int(fleet_summary.get('idle_slots') or max(int(fleet_summary.get('active_slots') or len(fleet_slots)) - sum(1 for slot in fleet_slots if slot.get('in_flight')), 0))}",
            "# HELP auto_router_fleet_worker_slots Number of worker slots in the dispatcher.",
            "# TYPE auto_router_fleet_worker_slots gauge",
            f"auto_router_fleet_worker_slots {int(fleet_summary.get('worker_slots') or 0)}",
            "# HELP auto_router_fleet_reviewer_slots Number of reviewer slots in the dispatcher.",
            "# TYPE auto_router_fleet_reviewer_slots gauge",
            f"auto_router_fleet_reviewer_slots {int(fleet_summary.get('reviewer_slots') or 0)}",
            "# HELP auto_router_fleet_completed_total Total completed fleet tasks.",
            "# TYPE auto_router_fleet_completed_total gauge",
            f"auto_router_fleet_completed_total {int(fleet_summary.get('completed') or 0)}",
            "# HELP auto_router_fleet_success_total Total successful fleet tasks.",
            "# TYPE auto_router_fleet_success_total gauge",
            f"auto_router_fleet_success_total {int(fleet_summary.get('success') or 0)}",
            "# HELP auto_router_fleet_failure_total Total failed fleet tasks.",
            "# TYPE auto_router_fleet_failure_total gauge",
            f"auto_router_fleet_failure_total {int(fleet_summary.get('failure') or 0)}",
            "# HELP auto_router_outbox_pressure_total Combined pending/retry/dead-letter outbox pressure.",
            "# TYPE auto_router_outbox_pressure_total gauge",
            f"auto_router_outbox_pressure_total {int(outbox_pressure.get('pressure_total') or 0)}",
            "# HELP auto_router_outbox_pressure_active Whether the router should treat outbox backlog as operational pressure.",
            "# TYPE auto_router_outbox_pressure_active gauge",
            f"auto_router_outbox_pressure_active {1 if outbox_pressure.get('active') else 0}",
            "# HELP auto_router_workflow_contract_completed_tasks Completed tasks captured by the current workflow contract snapshot.",
            "# TYPE auto_router_workflow_contract_completed_tasks gauge",
            f"auto_router_workflow_contract_completed_tasks {int(workflow_contract.get('completed_tasks') or 0)}",
            "# HELP auto_router_workflow_contract_open_gaps Number of remaining rollout gaps in the workflow contract.",
            "# TYPE auto_router_workflow_contract_open_gaps gauge",
            f"auto_router_workflow_contract_open_gaps {int(len(workflow_contract.get('changeover_gaps') or []))}",
            "# HELP auto_router_workflow_contract_review_queue_depth Live review queue depth used by the workflow contract snapshot.",
            "# TYPE auto_router_workflow_contract_review_queue_depth gauge",
            f"auto_router_workflow_contract_review_queue_depth {int(fleet_queues.get('review') or 0)}",
            "# HELP auto_router_workflow_contract_worker_queue_depth Live worker queue depth used by the workflow contract snapshot.",
            "# TYPE auto_router_workflow_contract_worker_queue_depth gauge",
            f"auto_router_workflow_contract_worker_queue_depth {int(fleet_queues.get('worker') or 0)}",
            "# HELP auto_router_workflow_contract_handoff_stage_present Whether handoff-stage traffic is present in the snapshot.",
            "# TYPE auto_router_workflow_contract_handoff_stage_present gauge",
            f"auto_router_workflow_contract_handoff_stage_present {1 if int((workflow_contract.get('stage_counts') or {}).get('handoff') or 0) > 0 else 0}",
            "# HELP auto_router_workflow_contract_alert_active Whether the rollout alert panel is actively warning about the workflow changeover.",
            "# TYPE auto_router_workflow_contract_alert_active gauge",
            f"auto_router_workflow_contract_alert_active {1 if workflow_contract_alert.get('active') else 0}",
        ]
    )
    for stage_name, count in (workflow_contract.get("stage_counts") or {}).items():
        lines.append('auto_router_workflow_contract_stage_count{stage="%s"} %s' % (stage_name, int(count or 0)))
    for task_kind, count in (fleet_stats.get("by_task_kind") or {}).items():
        lines.append('auto_router_fleet_tasks_by_kind{kind="%s"} %s' % (task_kind, int(count or 0)))
    for lane_name, count in (fleet_stats.get("by_lane") or {}).items():
        lines.append('auto_router_fleet_tasks_by_lane{lane="%s"} %s' % (lane_name, int(count or 0)))
    for source_name, count in (fleet_stats.get("by_source") or {}).items():
        lines.append('auto_router_fleet_tasks_by_source{source="%s"} %s' % (source_name, int(count or 0)))
    for outcome_name, count in (fleet_stats.get("by_outcome") or {}).items():
        lines.append('auto_router_fleet_tasks_by_outcome{outcome="%s"} %s' % (outcome_name, int(count or 0)))
    for reason_name, count in (fleet_stats.get("by_failure_reason") or {}).items():
        lines.append('auto_router_fleet_task_failures_by_reason{reason="%s"} %s' % (reason_name, int(count or 0)))
    for node_name, count in (fleet_stats.get("by_node") or {}).items():
        lines.append('auto_router_fleet_tasks_completed_total{node="%s"} %s' % (node_name, int(count or 0)))
    for role_name, count in (fleet_stats.get("by_role") or {}).items():
        lines.append('auto_router_fleet_tasks_by_role{role="%s"} %s' % (role_name, int(count or 0)))
    for stage_name, count in (fleet_stats.get("by_stage") or {}).items():
        lines.append('auto_router_fleet_tasks_by_stage{stage="%s"} %s' % (stage_name, int(count or 0)))
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
        return {"total": 0, "node": 0, "provider": 0, "model": 0, "service": 0, "signal": 0}
    if hasattr(state.context, "graph_object_summary"):
        return state.context.graph_object_summary()
    return {"total": 0, "node": 0, "provider": 0, "model": 0, "service": 0, "signal": 0}


def _context_signal_summary(state: Any) -> dict[str, int]:
    if not hasattr(state, "context") or not hasattr(state.context, "signal_summary"):
        return {"total": 0, "active": 0, "provider": 0, "model": 0, "node": 0, "service": 0}
    return state.context.signal_summary()


def _context_route_signal_summary(state: Any) -> dict[str, int]:
    if not hasattr(state, "context") or not hasattr(state.context, "all_signals"):
        return {"total": 0, "active": 0, "preferred": 0, "blocked": 0, "realtime": 0, "avoid": 0}
    signals = [signal for signal in state.context.all_signals() if str(getattr(signal, "source", "")).startswith("route_")]
    return {
        "total": len(signals),
        "active": sum(1 for signal in signals if signal.is_active),
        "preferred": sum(1 for signal in signals if signal.signal_type == "preferred"),
        "blocked": sum(1 for signal in signals if signal.signal_type == "blocked"),
        "realtime": sum(1 for signal in signals if signal.signal_type == "realtime"),
        "avoid": sum(1 for signal in signals if signal.signal_type == "avoid"),
    }


def _provider_tap_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    return _provider_tap_metrics(records)


def _provider_tap_metrics(provider_health: list[dict[str, Any]]) -> dict[str, int]:
    avg_latency_ms = int(mean([int(report.get("latency_ms") or 0) for report in provider_health if report.get("latency_ms") is not None])) if any(report.get("latency_ms") is not None for report in provider_health) else 0
    ok = sum(1 for report in provider_health if report.get("ok"))
    error = sum(1 for report in provider_health if not report.get("ok"))
    drift = sum(1 for report in provider_health if report.get("drift"))
    models = sum(int(report.get("model_count") or 0) for report in provider_health)
    healthy = sum(1 for report in provider_health if report.get("ok") and not report.get("drift"))
    return {
        "providers": len(provider_health),
        "ok": ok,
        "error": error,
        "models": models,
        "healthy": healthy,
        "drift": drift,
        "avg_latency_ms": avg_latency_ms,
    }


def _cli_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(items),
        "installed": sum(1 for item in items if item.get("installed")),
        "runnable": sum(1 for item in items if item.get("runnable")),
        "missing": sum(1 for item in items if not item.get("installed")),
    }
