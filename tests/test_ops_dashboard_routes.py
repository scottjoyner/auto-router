from types import SimpleNamespace
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
                "recent": [],
            }
        ]


class LiveModels:
    def snapshot(self):
        return [{"provider": "cerebras", "ok": True, "model_count": 3, "stale": False}]


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
        event_outbox=Outbox(),
        model_registry=ModelRegistry(),
        live_models=LiveModels(),
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
    templates = Jinja2Templates(directory="src/auto_router/templates")
    html_ops = templates.get_template("fragments/ops_summary.html").render(**summary)
    assert "Outbox dispatch" in html_ops
    assert "running" in html_ops
    assert "scheduled" in html_ops
    assert summary["model_registry_summary"]["models"] == 12
    assert summary["provider_tap_summary"]["providers"] == 2
    assert summary["provider_tap_summary"]["ok"] == 1
    assert summary["provider_tap_summary"]["drift"] == 1
    assert summary["provider_health_summary"][0]["health_score"] == 87
    assert summary["runtime_summary"]["samples"] == 2
    assert summary["runtime_summary"]["avg_value_per_second"] == 8.75
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
    assert summary["swarm_recent_probes"] == []
    assert summary["swarm_recent_snapshots"] == []
    assert summary["assistx_tasks_configured"] is True
    assert summary["assistx_event_sink_configured"] is True
    get_settings.cache_clear()


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
        context=context,
        context_graph_summary=context.graph_object_summary(),
        runtime_summary={"samples": 2, "successful": 2, "failed": 0, "avg_latency_ms": 42, "avg_elapsed_ms": 44, "avg_queue_wait_ms": 8, "avg_load_time_ms": 3, "avg_tokens_per_second": 12.5, "avg_value_per_second": 8.75, "by_provider": [{"provider": "cerebras", "samples": 2, "avg_tokens_per_second": 12.5, "avg_value_per_second": 8.75, "avg_queue_wait_ms": 8, "avg_load_time_ms": 3}]},
        recent_runtime_samples=[{"provider_id": "cerebras", "model_id": "gpt-oss-120b", "status_code": 200, "elapsed_ms": 44, "queue_wait_ms": 8, "load_time_ms": 3, "tokens_per_second": 12.5, "value_per_second": 8.75, "value_units": 375}],
        swarm_summary={"nodes": 1, "models": 2, "avg_endpoint_health_score": 50, "drift_providers": 1},
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
        recent_usage=[],
    )

    assert "Swarm memory map" in html
    assert "R2D2" in html
    assert "mixed" in html
    assert "Machine → model memory and endpoint health" in html
    assert "Local models vs API models" in html
    assert "lfm2.5-1.2b" in html
    assert "qwen3.5-opus" in html
    assert "fast backstop" in html
    assert "reasoning lane" in html
    text = render_ops_metrics(
        {
            "outbox_summary": {"pending": 2, "retry": 1, "delivered": 5, "dead_letter": 0},
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
            "assistx_tasks_configured": True,
            "assistx_event_sink_configured": False,
        }
    )

    assert 'auto_router_outbox_events{state="pending"} 2' in text
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
    assert "auto_router_swarm_nodes 1" in text
    assert "auto_router_swarm_models 2" in text
    assert "auto_router_swarm_services 2" in text
    assert "auto_router_swarm_avg_provider_health 87" in text
    assert "auto_router_swarm_avg_endpoint_health 50" in text
    assert "auto_router_swarm_drift_providers 1" in text
    assert "auto_router_swarm_recent_probes 1" in text

    assert "auto_router_assistx_tasks_configured 1" in text
    assert "auto_router_assistx_event_sink_configured 0" in text
