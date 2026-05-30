from types import SimpleNamespace

from auto_router.context import ContextService, ContextSnapshot, ServiceStatus
from auto_router.event_outbox import EventOutbox
from auto_router.service_routes import (
    apply_service_results_to_context,
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
