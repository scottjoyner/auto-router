from __future__ import annotations

import asyncio
from types import SimpleNamespace

import auto_router.main as main_module
from auto_router.quota import InMemoryQuotaManager


class FakeContext:
    revision = "rev-1"
    source = "unit-test"

    def local_provider_names(self):
        return ["local"]

    def free_api_provider_names(self):
        return []

    def blocked_provider_names(self):
        return ["blocked"]

    def running_local_node_names(self):
        return ["r2d2"]


class FakeCircuits:
    def snapshot(self):
        return []


class FakeProviders:
    def enabled(self):
        return ["lmstudio"]


class FakeAgents:
    agent_workers = []


async def _fake_gateway_status() -> dict[str, object]:
    return {"enabled": False, "ok": True, "mode": "direct", "detail": "mock"}


def test_health_reports_warning_outbox_pressure_without_degrading(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "build_agentgateway_status", _fake_gateway_status)
    monkeypatch.setattr(
        main_module,
        "state",
        SimpleNamespace(
            context=FakeContext(),
            circuits=FakeCircuits(),
            providers=FakeProviders(),
            agents=FakeAgents(),
            quota_backend="FakeQuota",
            outbox_dispatch_status={
                "status": "running",
                "last_outcome": "running",
                "last_reason": "scheduled",
                "last_started_at": 90,
                "last_completed_at": None,
                "last_duration_ms": None,
                "interval_seconds": 300.0,
                "next_run_at": 395.0,
                "last_summary": {"pending": 2, "retry": 1, "delivered": 5, "dead_letter": 0},
            },
        ),
    )

    result = asyncio.run(main_module.health())

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["gateway"]["ok"] is True
    assert result["free_api_providers"] == []
    assert result["assistx_outbox_dispatch"]["status"] == "running"
    assert result["assistx_outbox_dispatch"]["last_reason"] == "scheduled"
    assert result["assistx_outbox_dispatch"]["next_run_in_seconds"] >= 0
    assert result["assistx_outbox_pressure"]["level"] == "warning"
    assert result["assistx_outbox_pressure"]["pressure_total"] == 3


def _health_state(**overrides) -> SimpleNamespace:
    values = dict(
        context=FakeContext(),
        circuits=FakeCircuits(),
        providers=FakeProviders(),
        agents=FakeAgents(),
        quota_backend="FakeQuota",
        outbox_dispatch_status={
            "status": "running",
            "last_outcome": "running",
            "last_reason": "scheduled",
            "last_started_at": 90,
            "last_completed_at": None,
            "last_duration_ms": None,
            "interval_seconds": 300.0,
            "next_run_at": 395.0,
            "last_summary": {"pending": 2, "retry": 1, "delivered": 5, "dead_letter": 0},
        },
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_health_reports_redis_fallback_instead_of_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "build_agentgateway_status", _fake_gateway_status)
    reason = "Redis did not become ready within 120s (4 attempt(s))"
    quota = InMemoryQuotaManager(redis_fallback_reason=reason, redis_fallback_attempts=4)
    monkeypatch.setattr(main_module, "state", _health_state(quota=quota, quota_backend="InMemoryQuotaManager"))

    result = asyncio.run(main_module.health())

    assert result["deps"]["redis"]["status"] == "fallback_in_memory"
    assert result["deps"]["redis"]["detail"] == reason
    assert result["quota_backend"] == "InMemoryQuotaManager"


def test_health_keeps_not_configured_when_no_redis_was_configured(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "build_agentgateway_status", _fake_gateway_status)
    monkeypatch.setattr(main_module, "state", _health_state(quota=InMemoryQuotaManager()))

    result = asyncio.run(main_module.health())

    assert result["deps"]["redis"] == {"status": "not_configured"}


def test_router_request_marks_private_payload_local_only() -> None:
    request = main_module._router_request(
        "chat_completions",
        {"model": "auto/fast", "metadata": {"privacy": "internal"}},
    )

    assert request.local_only is True
    assert request.allow_cloud is False


def test_router_request_marks_sophia_model_local_only() -> None:
    request = main_module._router_request("chat_completions", {"model": "auto/sophia"})

    assert request.local_only is True
    assert request.allow_cloud is False
