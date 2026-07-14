from types import SimpleNamespace
import time
from fastapi.templating import Jinja2Templates

from auto_router.context import ContextModel, ContextNode, ContextProvider, ContextService, ContextSignal, ContextSnapshot, ExecutionLane, ServiceStatus
from auto_router.ops_dashboard_routes import build_ops_summary, render_ops_metrics


class Outbox:
    def summary(self):
        return {"pending": 2, "retry": 1, "delivered": 5, "dead_letter": 0, "total": 8}


class ModelRegistry:
    def summary(self):
        return {"providers": 2, "ok": 1, "error": 1, "models": 12, "stale": 1}

    def probe_summary(self):
        return {"providers": 2, "ok": 1, "error": 1, "models": 7, "drift": 1, "healthy": 1, "avg_latency_ms": 42}

    def provider_health_reports(self):
        return [
            {
                "provider": "cerebras",
                "health_score": 87,
                "ok": True,
                "drift": False,
                "model_count": 3,
                "latency_ms": 42,
                "last_fetched_at": 100,
                "age_seconds": 7,
                "success_rate": 1.0,
                "error": None,
                "signature": "abc",
                "previous_signature": "def",
                "recent": [
                    {"fetched_at": int(time.time()) - 7, "latency_ms": 42, "model_count": 3, "drift": False, "ok": True, "error": None},
                ],
            }
        ]


class LiveModels:
    def snapshot(self):
        return [
            {"provider": "cerebras", "ok": True, "model_count": 3, "stale": False, "fetched_at": 1000, "latency_ms": 42},
            {"provider": "lmstudio-r2d2", "ok": False, "model_count": 1, "stale": True, "fetched_at": 900, "latency_ms": 120, "error": "timeout"},
        ]


class Circuits:
    def snapshot(self):
        return [
            {"owner": "cerebras", "failures": 4, "opened_until": 200, "open": True, "last_error": "rate limited"},
            {"owner": "lmstudio-r2d2", "failures": 0, "opened_until": None, "open": False, "last_error": None},
        ]


class EventOutbox:
    def summary(self):
        return {"pending": 2, "retry": 1, "delivered": 5, "dead_letter": 1, "total": 9}

    def recent(self, limit: int = 24):
        return [
            {"event_type": "model-sync", "source_service": "router", "status": "dead_letter", "attempts": 3, "last_error": "timeout", "created_at": 1, "updated_at": 2},
            {"event_type": "probe", "source_service": "router", "status": "retry", "attempts": 2, "last_error": "timeout", "created_at": 3, "updated_at": 4},
            {"event_type": "other", "source_service": "router", "status": "delivered", "attempts": 0, "last_error": None, "created_at": 5, "updated_at": 6},
        ][:limit]


class Ledger:
    def runtime_summary(self):
        return {
            "samples": 2,
            "successful": 2,
            "failed": 0,
            "avg_latency_ms": 42,
            "avg_elapsed_ms": 44,
            "avg_queue_wait_ms": 8,
            "avg_load_time_ms": 3,
            "avg_tokens_per_second": 12.5,
            "avg_value_per_second": 8.75,
            "avg_value_units": 375,
            "by_provider": [
                {
                    "provider": "cerebras",
                    "samples": 2,
                    "successful": 2,
                    "failed": 0,
                    "avg_latency_ms": 42,
                    "avg_elapsed_ms": 44,
                    "avg_queue_wait_ms": 8,
                    "avg_load_time_ms": 3,
                    "avg_tokens_per_second": 12.5,
                    "avg_value_per_second": 8.75,
                }
            ],
        }

    def recent_runtime_samples(self, limit: int = 50):
        return [
            {
                "provider_id": "cerebras",
                "model_id": "gpt-oss-120b",
                "route": "chat_completions",
                "status_code": 200,
                "elapsed_ms": 44,
                "queue_wait_ms": 8,
                "load_time_ms": 3,
                "tokens_per_second": 12.5,
                "value_per_second": 8.75,
                "value_units": 375,
            }
        ][:limit]


def test_build_ops_summary_collects_runtime_state(monkeypatch) -> None:
    monkeypatch.setenv("AUTO_ROUTER_ASSISTX_TASKS_URL", "http://assistx/tasks")
    monkeypatch.setenv("AUTO_ROUTER_ASSISTX_EVENT_SINK_URL", "http://assistx/events")
    from auto_router.settings import get_settings

    get_settings.cache_clear()
    state = SimpleNamespace(
        event_outbox=EventOutbox(),
        model_registry=ModelRegistry(),
        live_models=LiveModels(),
        circuits=Circuits(),
        ledger=Ledger(),
        cli_discovery=[{"name": "gemini-cli", "installed": True, "runnable": True}],
        outbox_dispatch_status={
            "status": "running",
            "last_outcome": "running",
            "last_reason": "scheduled",
            "last_started_at": 90,
            "last_completed_at": 95,
            "last_duration_ms": 500,
            "interval_seconds": 300.0,
            "next_run_at": 395.0,
        },
        context=ContextSnapshot(
            nodes=[
                ContextNode(node_id="r2d2", display_name="R2D2", lane=ExecutionLane.local, running=True, detail="demo node"),
            ],
            providers=[
                ContextProvider(
                    provider="lmstudio-r2d2",
                    lane=ExecutionLane.local,
                    local=True,
                    can_use_free_api=False,
                    node_id="r2d2",
                    detail="local backstop",
                ),
                ContextProvider(
                    provider="cerebras",
                    lane=ExecutionLane.free_api,
                    local=False,
                    can_use_free_api=True,
                    node_id="r2d2",
                    detail="reasoning lane",
                ),
            ],
            services=[
                ContextService(service_id="a", name="A", url="http://a", status=ServiceStatus.online, node_id="r2d2", provider="lmstudio-r2d2"),
                ContextService(service_id="b", name="B", url="http://b", status=ServiceStatus.offline, node_id="r2d2", provider="cerebras"),
            ],
            models=[
                ContextModel(
                    model_id="local.lfm2.5-1.2b",
                    name="lfm2.5-1.2b",
                    provider="lmstudio-r2d2",
                    provider_model="local/lfm2.5-1.2b",
                    local=True,
                    can_use_free_api=False,
                    node_id="r2d2",
                    detail="fast docs and summarization",
                ),
                ContextModel(
                    model_id="api.qwen3.5-opus",
                    name="qwen3.5-opus",
                    provider="cerebras",
                    provider_model="qwen3.5-opus",
                    local=False,
                    can_use_free_api=True,
                    node_id="r2d2",
                    detail="reasoning lane",
                ),
                ContextModel(
                    model_id="blocked.sandbox",
                    name="sandbox",
                    provider="blocked",
                    provider_model="sandbox",
                    blocked=True,
                    local=False,
                    can_use_free_api=False,
                    lane=ExecutionLane.blocked,
                    detail="blocked for policy alignment",
                ),
            ],
            signals=[
                ContextSignal(
                    signal_id="node.r2d2.preferred",
                    target_type="node",
                    target_id="r2d2",
                    signal_type="preferred",
                    source="sophia",
                    strength=1.0,
                )
            ],
        ),
    )

    summary = build_ops_summary(state)

    assert summary["outbox_summary"]["pending"] == 2
    assert summary["outbox_dispatch_summary"]["status"] == "running"
    assert summary["circuit_breaker_summary"]["open"] == 1
    assert summary["dead_letter_summary"]["dead_letter"] == 1
    assert summary["dead_letter_reasons"][0]["reason"] == "timeout"
    assert summary["live_model_freshness"][0]["provider"] == "lmstudio-r2d2"
    templates = Jinja2Templates(directory="src/auto_router/templates")
    html_ops = templates.get_template("fragments/ops_summary.html").render(**summary)
    assert "Outbox dispatch" in html_ops
    assert "running" in html_ops
    assert "scheduled" in html_ops
    assert "Operational gaps" in html_ops
    assert "New workflow contract" in html_ops
    assert "Iterative review → final handoff" in html_ops
    assert "Plan steps" in html_ops
    assert "Validation and review" in html_ops
    assert "Remaining changeover gaps" in html_ops
    assert "No live dispatcher snapshot yet" in html_ops
    assert "1. Live model freshness" in html_ops
    assert "2. Circuit breaker status" in html_ops
    assert "3. Dead-letter triage" in html_ops
    assert "4. Node health freshness" in html_ops
    assert "5. Recent runtime trend" in html_ops
    assert "last probe 7s ago" in html_ops
    assert "rate limited" in html_ops
    assert "timeout" in html_ops
    assert "probe" in html_ops
    assert summary["model_registry_summary"]["models"] == 12
    assert summary["provider_tap_summary"]["providers"] == 2
    assert summary["provider_tap_summary"]["ok"] == 1
    assert summary["provider_tap_summary"]["drift"] == 1
    assert summary["provider_health_summary"][0]["health_score"] == 87
    assert summary["runtime_summary"]["samples"] == 2
    assert summary["runtime_summary"]["avg_value_per_second"] == 8.75
    assert summary["swarm_runtime_throughput"]["by_provider"]["cerebras"]["avg_tokens_per_second"] == 12.5
    assert summary["swarm_runtime_throughput"]["by_model"]["gpt-oss-120b"]["samples"] == 1
    assert summary["swarm_memory_map"][0]["runtime"]["avg_tokens_per_second"] == 12.5
    assert summary["recent_runtime_samples"][0]["provider_id"] == "cerebras"
    assert summary["cli_summary"]["runnable"] == 1
    assert summary["service_summary"]["online"] == 1
    assert summary["context_graph_summary"]["total"] == 9
    assert summary["context_signal_summary"]["total"] == 1
    assert summary["context_signal_summary"]["active"] == 1
    assert summary["context_signal_summary"]["node"] == 1
    assert summary["context_model_summary"]["local"] == 1
    assert summary["context_model_summary"]["api_models"] == 1
    assert summary["context_model_summary"]["blocked_models"] == 1
    assert summary["context_model_lane_summary"]["local"] == 1
    assert summary["context_model_lane_summary"]["free_api"] == 1
    assert summary["context_model_lane_summary"]["blocked"] == 1
    assert summary["swarm_summary"]["nodes"] == 1
    assert summary["swarm_summary"]["models"] == 3
    assert summary["swarm_summary"]["signals"] == 1
    assert summary["swarm_summary"]["avg_provider_health_score"] == 87
    assert summary["swarm_memory_map"][0]["node_id"] == "r2d2"
    assert summary["swarm_memory_map"][0]["model_count"] == 2
    assert summary["swarm_memory_map"][0]["signal_count"] == 1
    assert summary["swarm_memory_map"][0]["endpoint_health"]["score"] == 50
    assert summary["swarm_recent_probes"][0]["provider"] == "cerebras"
    assert summary["swarm_recent_probes"][0]["age_seconds"] == 7
    assert summary["swarm_recent_snapshots"] == []
    assert summary["assistx_tasks_configured"] is True
    assert summary["assistx_event_sink_configured"] is True
    assert summary["workflow_contract_summary"]["profile_name"] == "iterative_review_handoff"
    assert summary["workflow_contract_summary"]["workflow_stage"] == "handoff"
    assert summary["workflow_contract_summary"]["plan_steps"][0].startswith("Inspect the current state")
    assert summary["workflow_contract_summary"]["changeover_gaps"][0]["title"] == "No live dispatcher snapshot yet"
    assert summary["workflow_contract_summary"]["completed_tasks"] == 0
    assert summary["workflow_contract_alert"]["level"] == "critical"
    assert summary["workflow_contract_alert"]["active"] is True
    get_settings.cache_clear()


def test_workflow_contract_marks_handoff_confirmed_after_two_snapshots() -> None:
    from auto_router.ops_dashboard_routes import _workflow_contract_alert_summary, _workflow_contract_summary

    fleet_dispatcher_stats = {
        "queues": {"worker": 0, "review": 0},
        "stats": {
            "completed": 8,
            "success": 8,
            "failure": 0,
            "by_stage": {"work": 5, "review": 2, "handoff": 1},
            "recent_snapshots": [
                {"stats": {"by_stage": {"work": 4, "review": 2, "handoff": 1}}},
                {"stats": {"by_stage": {"work": 5, "review": 2, "handoff": 1}}},
            ],
        },
    }

    workflow_contract = _workflow_contract_summary(fleet_dispatcher_stats)
    workflow_alert = _workflow_contract_alert_summary(workflow_contract, fleet_dispatcher_stats)

    assert workflow_contract["handoff_snapshot_count"] == 2
    assert workflow_contract["handoff_confirmed"] is True
    assert workflow_contract["changeover_gaps"] == []
    assert workflow_alert["level"] == "ok"
    assert workflow_alert["handoff_confirmed"] is True
    assert workflow_alert["handoff_snapshot_count"] == 2


def test_dashboard_summary_renders_local_and_api_model_sections() -> None:
    templates = Jinja2Templates(directory="src/auto_router/templates")
    context = ContextSnapshot(
        services=[ContextService(service_id="svc", name="Service", url="http://svc", status=ServiceStatus.online)],
        models=[
            ContextModel(
                model_id="local.lfm2.5-1.2b",
                name="lfm2.5-1.2b",
                provider="lmstudio-r2d2",
                provider_model="local/lfm2.5-1.2b",
                local=True,
                can_use_free_api=False,
                detail="fast docs and summarization",
            ),
            ContextModel(
                model_id="api.qwen3.5-opus",
                name="qwen3.5-opus",
                provider="cerebras",
                provider_model="qwen3.5-opus",
                local=False,
                can_use_free_api=True,
                detail="reasoning lane",
            ),
        ],
    )

    html = templates.get_template("fragments/dashboard_summary.html").render(
        snapshots=[SimpleNamespace(provider="cerebras", model="gpt-oss-120b", dimensions={})],
        provider_health_summary=[
            {
                "provider": "cerebras",
                "health_score": 87,
                "model_count": 3,
                "success_rate": 1.0,
                "age_seconds": 7,
                "drift": False,
                "error": None,
            }
        ],
        provider_probe_summary={"providers": 1, "healthy": 1, "drift": 0, "avg_latency_ms": 42},
        gateway={"enabled": False, "ok": False, "mode": "direct", "detail": "not configured"},
        assistx_tasks_url="http://assistx:8000/api/routes/request",
        assistx_event_sink_url="http://assistx:8000/api/events",
        assistx_tasks_configured=True,
        assistx_event_sink_configured=True,
        outbox_summary={"pending": 5794, "retry": 1472, "delivered": 13873, "dead_letter": 0},
        outbox_dispatch_summary={"status": "running", "next_run_in_seconds": None},
        context=context,
        context_graph_summary=context.graph_object_summary(),
        runtime_summary={"samples": 2, "successful": 2, "failed": 0, "avg_latency_ms": 42, "avg_elapsed_ms": 44, "avg_queue_wait_ms": 8, "avg_load_time_ms": 3, "avg_tokens_per_second": 12.5, "avg_value_per_second": 8.75, "by_provider": [{"provider": "cerebras", "samples": 2, "avg_tokens_per_second": 12.5, "avg_value_per_second": 8.75, "avg_queue_wait_ms": 8, "avg_load_time_ms": 3}]},
        recent_runtime_samples=[{"provider_id": "cerebras", "model_id": "gpt-oss-120b", "status_code": 200, "elapsed_ms": 44, "queue_wait_ms": 8, "load_time_ms": 3, "tokens_per_second": 12.5, "value_per_second": 8.75, "value_units": 375}],
        swarm_summary={"nodes": 1, "models": 2, "avg_endpoint_health_score": 50, "drift_providers": 1},
        fleet_dispatcher_stats={
            "queues": {"review": 3, "worker": 1},
            "summary": {"active_slots": 2, "busy_slots": 1, "worker_slots": 1, "reviewer_slots": 1, "online_nodes": 1, "online_nodes_with_loaded_models": 1, "completed": 10, "success": 9, "failure": 1},
            "stats": {"by_stage": {"work": 8, "review": 2, "handoff": 1}, "by_node": {"r2d2": 10}},
            "slots": [{"node": "r2d2", "role": "reviewer", "model": "gpt-oss-120b", "completed": 10, "success": 9, "failure": 1, "in_flight": False}],
        },
        fleet_loadout_report={
            "snapshot_id": "snapshot-123",
            "captured_at": "2026-07-02T12:00:00Z",
            "loadouts": [
                {
                    "task_profile_name": "iterative_review_handoff",
                    "score": 98,
                    "rationale": "Best reviewer/handoff pairing",
                    "primary": {"node_name": "r2d2", "model_id": "gpt-oss-120b"},
                    "reviewer": {"node_name": "c3po", "model_id": "qwen3.5-opus"},
                }
            ],
        },
        workflow_contract_summary={
            "profile_name": "iterative_review_handoff",
            "workflow_stage": "handoff",
            "completed_tasks": 10,
            "plan_steps": ["Inspect the current state one slice at a time.", "Make the smallest safe change or conclusion.", "Validate the result against the acceptance criteria.", "Report risks, gaps, and handoff notes."],
            "validation_metrics": ["acceptance_criteria_met", "regressions_checked", "handoff_ready"],
            "review_checkpoints": ["reviewed by local iteration", "validated against plan", "final handoff approved"],
            "stage_counts": {"work": 8, "review": 2, "handoff": 1},
            "changeover_gaps": [{"title": "No observed handoff-stage traffic", "detail": "The dispatcher snapshot shows work and review traffic, but no completed handoff-stage work yet, so the final-review path is not proven in production traffic."}],
        },
        workflow_contract_alert={
            "active": True,
            "level": "warning",
            "headline": "Handoff-stage traffic has not shown up yet",
            "detail": "The live snapshot still shows work and review activity, but no completed handoff-stage traffic to prove the final leg is flowing.",
            "action": "Keep routing a few end-to-end jobs through the new path until handoff appears in the live stage counts.",
            "open_gaps": 1,
            "handoff_present": False,
            "review_queue_depth": 3,
            "worker_queue_depth": 1,
            "completed_tasks": 10,
        },
        swarm_memory_map=[
            {
                "display_name": "R2D2",
                "node_id": "r2d2",
                "lane": "local",
                "detail": "demo node",
                "model_count": 2,
                "provider_count": 2,
                "service_count": 2,
                "endpoint_health": {"state": "mixed", "score": 50},
                "models": ["local/lfm2.5-1.2b", "qwen3.5-opus"],
                "recent_history": [{"provider": "cerebras", "age_seconds": 7, "model_count": 3, "drift": False}],
            }
        ],
        swarm_recent_probes=[{"provider": "cerebras", "fetched_at": 100, "age_seconds": 7, "latency_ms": 42, "model_count": 3, "drift": False, "ok": True}],
        swarm_runtime_throughput={"by_provider": {"cerebras": {"samples": 2, "avg_tokens_per_second": 12.5, "avg_value_per_second": 8.75, "avg_queue_wait_ms": 8, "avg_latency_ms": 42}}, "by_model": {"gpt-oss-120b": {"samples": 1, "avg_tokens_per_second": 12.5, "avg_value_per_second": 8.75, "avg_queue_wait_ms": 8, "avg_latency_ms": 42}}},
        recent_usage=[],
        )

    assert "Swarm memory map" in html
    assert "R2D2" in html
    assert "mixed" in html
    assert "Machine → model memory and endpoint health" in html
    assert "Runtime tokens/sec by node and model" in html
    assert "tok/s avg" in html
    assert "lfm2.5-1.2b" in html
    assert "qwen3.5-opus" in html
    assert "next n/as" in html
    assert "fast backstop" in html
    assert "reasoning lane" in html
    assert "last probe 7s ago" in html
    assert "Operational gaps" in html
    assert "New workflow contract" in html
    assert "Iterative review → final handoff" in html
    assert "Rollout alert" in html
    assert "Handoff-stage traffic has not shown up yet" in html
    assert "Next move:" in html
    assert "Remaining changeover gaps" in html
    assert "handoff 1" in html
    assert "snapshot-123" in html
    assert "1. Live model freshness" in html
    assert "2. Circuit breaker status" in html
    assert "Unified router + AssistX control surface" in html
    assert "Backlog task routing" in html
    assert "AssistX dispatch path" in html
    assert "Pending" in html
    assert "running" in html
    assert "next n/a" in html
    text = render_ops_metrics(
        {
            "outbox_summary": {"pending": 2, "retry": 1, "delivered": 5, "dead_letter": 0},
            "outbox_pressure_summary": {"pressure_total": 3, "active": True},
            "model_registry_summary": {"providers": 2, "models": 12, "stale": 1},
            "cli_summary": {"total": 3, "installed": 2, "runnable": 1, "missing": 1},
            "service_summary": {"total": 4, "online": 2, "offline": 1, "degraded": 0, "unknown": 1, "blocked": 0},
            "context_model_summary": {"total": 4, "local": 1, "free_api": 2, "blocked": 1},
            "context_model_lane_summary": {"total": 4, "local": 1, "free_api": 2, "blocked": 1},
            "context_graph_summary": {"total": 9, "node": 1, "provider": 2, "model": 4, "service": 2},
            "provider_tap_summary": {"providers": 2, "ok": 1, "error": 1, "models": 7, "drift": 1},
            "provider_health_summary": [
                {
                    "provider": "cerebras",
                    "health_score": 87,
                    "age_seconds": 7,
                    "drift": False,
                    "model_count": 3,
                    "success_rate": 1.0,
                    "error": None,
                }
            ],
            "runtime_summary": {"samples": 2, "successful": 2, "failed": 0, "avg_latency_ms": 42, "avg_elapsed_ms": 44, "avg_queue_wait_ms": 8, "avg_load_time_ms": 3, "avg_tokens_per_second": 12.5, "avg_value_per_second": 8.75, "by_provider": [{"provider": "cerebras", "samples": 2, "avg_tokens_per_second": 12.5, "avg_value_per_second": 8.75, "avg_queue_wait_ms": 8, "avg_load_time_ms": 3}]},
            "recent_runtime_samples": [{"provider_id": "cerebras", "model_id": "gpt-oss-120b", "status_code": 200, "elapsed_ms": 44, "queue_wait_ms": 8, "load_time_ms": 3, "tokens_per_second": 12.5, "value_per_second": 8.75, "value_units": 375}],
            "swarm_summary": {"nodes": 1, "models": 2, "services": 2, "avg_provider_health_score": 87, "avg_endpoint_health_score": 50, "drift_providers": 1},
            "swarm_memory_map": [
                {"display_name": "R2D2", "node_id": "r2d2", "lane": "local", "detail": "demo node", "endpoint_health": {"state": "mixed", "score": 50}, "model_count": 2, "provider_count": 2, "service_count": 2, "models": ["local/lfm2.5-1.2b", "qwen3.5-opus"], "recent_history": [{"provider": "cerebras", "age_seconds": 7, "model_count": 3, "drift": False}]}
            ],
            "swarm_recent_probes": [{"provider": "cerebras", "fetched_at": 100, "age_seconds": 7, "latency_ms": 42, "model_count": 3, "drift": False, "ok": True}],
            "swarm_runtime_throughput": {"by_provider": {"cerebras": {"samples": 2, "avg_tokens_per_second": 12.5, "avg_value_per_second": 8.75, "avg_queue_wait_ms": 8, "avg_latency_ms": 42}}, "by_model": {"gpt-oss-120b": {"samples": 1, "avg_tokens_per_second": 12.5, "avg_value_per_second": 8.75, "avg_queue_wait_ms": 8, "avg_latency_ms": 42}}},
                "fleet_dispatcher_stats": {"queues": {"review": 3, "worker": 1}, "summary": {"active_slots": 2, "busy_slots": 1, "worker_slots": 1, "reviewer_slots": 1, "online_nodes": 1, "online_nodes_with_loaded_models": 1, "completed": 10, "success": 9, "failure": 1}, "stats": {"by_stage": {"work": 8, "review": 2, "handoff": 1}, "by_node": {"r2d2": 10}}, "slots": [{"node": "r2d2", "role": "reviewer", "model": "gpt-oss-120b", "completed": 10, "success": 9, "failure": 1, "in_flight": False}]},
                "assistx_tasks_configured": True,
            "assistx_event_sink_configured": False,
            "workflow_contract_summary": {
                "profile_name": "iterative_review_handoff",
                "workflow_stage": "handoff",
                "completed_tasks": 10,
                "plan_steps": ["Inspect the current state one slice at a time."],
                "validation_metrics": ["acceptance_criteria_met"],
                "review_checkpoints": ["final handoff approved"],
                "stage_counts": {"work": 8, "review": 2, "handoff": 1},
                "changeover_gaps": [{"title": "No observed handoff-stage traffic", "detail": "The dispatcher snapshot shows work and review traffic, but no completed handoff-stage work yet, so the final-review path is not proven in production traffic."}],
            },
            "workflow_contract_alert": {
                "active": True,
                "level": "warning",
                "headline": "Handoff-stage traffic has not shown up yet",
                "detail": "The live snapshot still shows work and review activity, but no completed handoff-stage traffic to prove the final leg is flowing.",
                "action": "Keep routing a few end-to-end jobs through the new path until handoff appears in the live stage counts.",
                "open_gaps": 1,
                "handoff_present": False,
                "review_queue_depth": 3,
                "worker_queue_depth": 1,
                "completed_tasks": 10,
            },
        }
    )

    assert 'auto_router_outbox_events{state="pending"} 2' in text
    assert "auto_router_outbox_pressure_total 3" in text
    assert "auto_router_outbox_pressure_active 1" in text
    assert "auto_router_model_registry_models 12" in text
    assert 'auto_router_agent_cli_tools{state="runnable"} 1' in text
    assert 'auto_router_services{status="online"} 2' in text
    assert "auto_router_provider_taps 2" in text
    assert "auto_router_provider_tap_models 7" in text
    assert "auto_router_provider_probe_drift 1" in text
    assert 'auto_router_provider_health_score{provider="cerebras"} 87' in text
    assert "auto_router_context_models 4" in text
    assert "auto_router_context_model_local 1" in text
    assert "auto_router_context_model_free_api 2" in text
    assert "auto_router_context_model_blocked 1" in text
    assert "auto_router_context_graph_objects 9" in text
    assert "auto_router_provider_taps 2" in text
    assert "auto_router_runtime_samples 2" in text
    assert "auto_router_runtime_avg_tokens_per_second 12.5" in text
    assert "auto_router_runtime_provider_samples{provider=\"cerebras\"} 2" in text
    assert "auto_router_runtime_provider_avg_value_per_second{provider=\"cerebras\"} 8.75" in text
    assert 'auto_router_runtime_model_samples{model="gpt-oss-120b"} 1' in text
    assert 'auto_router_runtime_model_avg_tokens_per_second{model="gpt-oss-120b"} 12.5' in text
    assert "auto_router_swarm_nodes 1" in text
    assert "auto_router_swarm_models 2" in text
    assert "auto_router_swarm_services 2" in text
    assert "auto_router_swarm_avg_provider_health 87" in text
    assert "auto_router_swarm_avg_endpoint_health 50" in text
    assert "auto_router_swarm_drift_providers 1" in text
    assert "auto_router_swarm_recent_probes 1" in text
    assert "auto_router_fleet_failure_total 1" in text
    assert "auto_router_workflow_contract_completed_tasks 10" in text
    assert "auto_router_workflow_contract_open_gaps 1" in text
    assert "auto_router_workflow_contract_review_queue_depth 3" in text
    assert "auto_router_workflow_contract_worker_queue_depth 1" in text
    assert "auto_router_workflow_contract_handoff_stage_present 1" in text
    assert "auto_router_workflow_contract_alert_active 1" in text
    assert 'auto_router_workflow_contract_stage_count{stage="handoff"} 1' in text
    assert 'auto_router_fleet_tasks_by_stage{stage="work"} 8' in text

    assert "auto_router_assistx_tasks_configured 1" in text
    assert "auto_router_assistx_event_sink_configured 0" in text
