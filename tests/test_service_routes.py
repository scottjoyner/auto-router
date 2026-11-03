from types import SimpleNamespace

import asyncio

from auto_router.context import ContextService, ContextSnapshot, ServiceStatus
from auto_router.event_outbox import EventOutbox, OutboxEvent
from auto_router.service_routes import (
    apply_service_results_to_context,
    build_outbox_dispatch_status,
    build_outbox_pressure_status,
    dispatch_outbox_cycle,
    enqueue_service_snapshot_events,
    service_summary,
)
from auto_router.service_scanner import ServiceProbeResult


def test_apply_service_results_to_context_updates_all_service_levels() -> None:
    context = ContextSnapshot.model_validate(
        {
            "services": [
                {"service_id": "global", "name": "Global", "url": "http://localhost", "status": "unknown"}
            ],
            "nodes": [
                {
                    "node_id": "deathstar",
                    "services": [
                        {"service_id": "node", "name": "Node", "url": "http://deathstar", "status": "unknown"}
                    ],
                }
            ],
            "providers": [
                {
                    "provider": "cerebras",
                    "services": [
                        {"service_id": "provider", "name": "Provider", "url": "https://api.example", "status": "unknown"}
                    ],
                }
            ],
        }
    )
    results = [
        ServiceProbeResult("global", "Global", "http://localhost", ServiceStatus.online, 1),
        ServiceProbeResult("node", "Node", "http://deathstar", ServiceStatus.offline, 1),
        ServiceProbeResult("provider", "Provider", "https://api.example", ServiceStatus.degraded, 1),
    ]

    updated = apply_service_results_to_context(context, results)

    assert updated.services[0].status == ServiceStatus.online
    assert updated.nodes[0].services[0].status == ServiceStatus.offline
    assert updated.providers[0].services[0].status == ServiceStatus.degraded


def test_service_summary_counts_statuses() -> None:
    services = [
        ContextService(service_id="a", name="A", url="http://a", status="online"),
        ContextService(service_id="b", name="B", url="http://b", status="offline"),
        ContextService(service_id="c", name="C", url="http://c", status="blocked"),
        ContextService(service_id="d", name="D", url="http://d"),
    ]

    summary = service_summary(services)

    assert summary["total"] == 4
    assert summary["online"] == 1
    assert summary["offline"] == 1
    assert summary["blocked"] == 1
    assert summary["unknown"] == 1


def test_enqueue_service_snapshot_events(tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    state = SimpleNamespace(
        event_outbox=outbox,
        context=ContextSnapshot(revision="rev-1", source="unit-test"),
    )
    results = [
        ServiceProbeResult(
            service_id="neo4j.browser",
            name="Neo4j Browser",
            url="http://localhost:7474",
            status=ServiceStatus.online,
            checked_at=123,
            latency_ms=12,
            status_code=200,
        )
    ]

    event_ids = enqueue_service_snapshot_events(state, results)
    events = outbox.pending()

    assert len(event_ids) == 1
    assert len(events) == 1
    assert events[0]["event_type"] == "router.service_snapshot.recorded"
    assert events[0]["payload"]["service_id"] == "neo4j.browser"
    assert events[0]["payload"]["context_revision"] == "rev-1"
    assert events[0]["payload"]["context_source"] == "unit-test"


def test_build_outbox_pressure_status_flags_small_backlog(tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    outbox.enqueue(
        OutboxEvent(
            event_type="router.service_snapshot.recorded",
            idempotency_key="service:a:1:online",
            payload={"service_id": "a"},
        )
    )
    state = SimpleNamespace(event_outbox=outbox, outbox_dispatch_status={})

    pressure = build_outbox_pressure_status(state)

    assert pressure["level"] == "warning"
    assert pressure["pressure_total"] == 1
    assert pressure["active"] is True


def test_dispatch_outbox_cycle_updates_status(monkeypatch, tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    outbox.enqueue(
        OutboxEvent(
            event_type="router.service_snapshot.recorded",
            idempotency_key="service:a:1:online",
            payload={"service_id": "a"},
        )
    )
    state = SimpleNamespace(event_outbox=outbox, outbox_dispatch_lock=asyncio.Lock(), outbox_dispatch_status={})

    class FakeDispatcher:
        configured = True

        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def dispatch_pending(self, limit: int = 25, dry_run: bool = False):
            assert limit == 3
            assert dry_run is False
            return [SimpleNamespace(to_dict=lambda: {"event_id": "evt-1", "status": "delivered"})]

    import auto_router.service_routes as service_routes

    monkeypatch.setattr(service_routes, "AssistXEventDispatcher", FakeDispatcher)
    monkeypatch.setattr(
        service_routes,
        "get_settings",
        lambda: SimpleNamespace(
            assistx_event_sink_url="http://assistx.test/events",
            assistx_event_dispatch_timeout_seconds=1.5,
            assistx_event_dispatch_max_attempts=4,
            assistx_event_dispatch_interval_seconds=5.0,
            assistx_basic_auth_user="",
            assistx_basic_auth_pass="",
        ),
    )

    result = asyncio.run(dispatch_outbox_cycle(state, limit=3, dry_run=False, reason="scheduled"))
    status = build_outbox_dispatch_status(state)

    assert result["configured"] is True
    assert status["running"] is False
    assert status["status"] == "idle"
    assert status["last_outcome"] == "success"
    assert status["last_reason"] == "scheduled"
    assert status["last_result_count"] == 1
    assert status["interval_seconds"] == 5.0
    assert status["pending"] == 1


def _scheduled_outbox_state(*, pending=60, retry=0, dead_letter=0,
                            outcome="success", age=245, interval=300.0,
                            next_in=55, enabled=True, dry_run=False):
    # A fixed time source avoids sleeping or invoking a real dispatcher.
    now = 1000.0
    def live_summary():
        return {"pending": pending, "retry": retry,
                "dead_letter": dead_letter, "delivered": 9876,
                "total": pending + retry + dead_letter + 9876}
    return SimpleNamespace(
        event_outbox=SimpleNamespace(summary=live_summary),
        outbox_dispatch_status={
            "configured": enabled, "enabled": enabled,
            "last_outcome": outcome,
            "last_dry_run": dry_run,
            "last_completed_at": now - age,
            "interval_seconds": interval,
            "next_run_at": now + next_in,
            # A historical summary must never override the live queue.
            "last_summary": {"pending": 74, "retry": 15, "dead_letter": 4,
                             "delivered": 9876},
        },
    )


def test_expected_scheduled_event_batch_is_not_false_pressure(monkeypatch):
    import auto_router.service_routes as routes
    monkeypatch.setattr(routes.time, "time", lambda: 1000.0)
    state = _scheduled_outbox_state(pending=60)
    pressure = routes.build_outbox_pressure_status(state)
    assert pressure["pressure_count_source"] == "live_outbox"
    assert pressure["pending"] == 60
    assert pressure["retry"] == 0
    assert pressure["dead_letter"] == 0
    assert pressure["scheduled_drain_expected"] is True
    assert pressure["pressure_total"] == 60
    assert pressure["level"] == "ok"
    assert pressure["active"] is False
    assert "not verified inference" in pressure["detail"]


def test_zero_live_pending_never_resurrects_old_dispatch_summary(monkeypatch):
    import auto_router.service_routes as routes
    monkeypatch.setattr(routes.time, "time", lambda: 1000.0)
    result = routes.build_outbox_pressure_status(_scheduled_outbox_state(pending=0))
    assert result["pressure_count_source"] == "live_outbox"
    assert result["pending"] == 0
    assert result["retry"] == 0
    assert result["dead_letter"] == 0
    assert result["pressure_total"] == 0
    assert result["level"] == "ok"
    assert result["scheduled_drain_expected"] is False


def test_overdue_failed_dry_run_or_disabled_schedule_does_not_suppress_alert(monkeypatch):
    import auto_router.service_routes as routes
    monkeypatch.setattr(routes.time, "time", lambda: 1000.0)
    cases = [
        {"age": 345, "next_in": -45},
        {"outcome": "failed"},
        {"dry_run": True},
        {"enabled": False},
        {"retry": 1},
        {"dead_letter": 1},
        {"pending": 150},
    ]
    for changes in cases:
        state = _scheduled_outbox_state(**changes)
        result = routes.build_outbox_pressure_status(state)
        assert result["scheduled_drain_expected"] is False, changes
        assert result["level"] == "warning", changes
        assert result["active"] is True, changes


def test_large_sustained_backlog_remains_critical_even_with_successful_prior_drain(monkeypatch):
    import auto_router.service_routes as routes
    monkeypatch.setattr(routes.time, "time", lambda: 1000.0)
    result = routes.build_outbox_pressure_status(_scheduled_outbox_state(pending=201))
    assert result["scheduled_drain_expected"] is False
    assert result["level"] == "critical"
    assert result["pressure_total"] == 201


def test_historical_status_only_fallback_is_labeled_and_not_treated_as_live_drain(monkeypatch):
    import auto_router.service_routes as routes
    monkeypatch.setattr(routes.time, "time", lambda: 1000.0)
    live = _scheduled_outbox_state(pending=50)
    state = SimpleNamespace(outbox_dispatch_status=live.outbox_dispatch_status)
    result = routes.build_outbox_pressure_status(state)
    assert result["pressure_count_source"] == "last_dispatch_snapshot"
    assert result["pending"] == 74
    assert result["retry"] == 15
    assert result["dead_letter"] == 4
    assert result["scheduled_drain_expected"] is False
    assert result["level"] == "warning"
