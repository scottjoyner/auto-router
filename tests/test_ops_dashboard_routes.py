from types import SimpleNamespace

from auto_router.context import ContextService, ContextSnapshot
from auto_router.ops_dashboard_routes import build_ops_summary, render_ops_metrics


class Outbox:
    def summary(self):
        return {"pending": 2, "retry": 1, "delivered": 5, "dead_letter": 0, "total": 8}


class ModelRegistry:
    def summary(self):
        return {"providers": 2, "ok": 1, "error": 1, "models": 12, "stale": 1}


class LiveModels:
    def snapshot(self):
        return [{"provider": "cerebras", "ok": True, "model_count": 3, "stale": False}]


def test_build_ops_summary_collects_runtime_state(monkeypatch) -> None:
    monkeypatch.setenv("AUTO_ROUTER_ASSISTX_TASKS_URL", "http://assistx/tasks")
    monkeypatch.setenv("AUTO_ROUTER_ASSISTX_EVENT_SINK_URL", "http://assistx/events")
    from auto_router.settings import get_settings

    get_settings.cache_clear()
    state = SimpleNamespace(
        event_outbox=Outbox(),
        model_registry=ModelRegistry(),
        live_models=LiveModels(),
        cli_discovery=[{"name": "gemini-cli", "installed": True, "runnable": True}],
        context=ContextSnapshot(
            services=[
                ContextService(service_id="a", name="A", url="http://a", status="online"),
                ContextService(service_id="b", name="B", url="http://b", status="offline"),
            ]
        ),
    )

    summary = build_ops_summary(state)

    assert summary["outbox_summary"]["pending"] == 2
    assert summary["model_registry_summary"]["models"] == 12
    assert summary["cli_summary"]["runnable"] == 1
    assert summary["service_summary"]["online"] == 1
    assert summary["assistx_tasks_configured"] is True
    assert summary["assistx_event_sink_configured"] is True
    get_settings.cache_clear()


def test_render_ops_metrics_outputs_prometheus_text() -> None:
    text = render_ops_metrics(
        {
            "outbox_summary": {"pending": 2, "retry": 1, "delivered": 5, "dead_letter": 0},
            "model_registry_summary": {"providers": 2, "models": 12, "stale": 1},
            "cli_summary": {"total": 3, "installed": 2, "runnable": 1, "missing": 1},
            "service_summary": {"total": 4, "online": 2, "offline": 1, "degraded": 0, "unknown": 1, "blocked": 0},
            "assistx_tasks_configured": True,
            "assistx_event_sink_configured": False,
        }
    )

    assert 'auto_router_outbox_events{state="pending"} 2' in text
    assert "auto_router_model_registry_models 12" in text
    assert 'auto_router_agent_cli_tools{state="runnable"} 1' in text
    assert 'auto_router_services{status="online"} 2' in text
    assert "auto_router_assistx_tasks_configured 1" in text
    assert "auto_router_assistx_event_sink_configured 0" in text
