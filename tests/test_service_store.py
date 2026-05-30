from auto_router.context import ServiceStatus
from auto_router.service_scanner import ServiceProbeResult
from auto_router.service_store import ServiceStatusStore


def test_service_status_store_saves_and_reads_latest_results(tmp_path) -> None:
    store = ServiceStatusStore(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    first = ServiceProbeResult(
        service_id="neo4j",
        name="Neo4j",
        url="http://localhost:7474",
        status=ServiceStatus.offline,
        checked_at=1,
        latency_ms=100,
    )
    second = ServiceProbeResult(
        service_id="neo4j",
        name="Neo4j",
        url="http://localhost:7474",
        status=ServiceStatus.online,
        checked_at=2,
        latency_ms=25,
        status_code=200,
    )

    store.save_results([first])
    store.save_results([second])

    latest = store.latest_results()
    assert len(latest) == 1
    assert latest[0].service_id == "neo4j"
    assert latest[0].status == ServiceStatus.online
    assert latest[0].latency_ms == 25


def test_service_status_store_summary_counts_latest_status(tmp_path) -> None:
    store = ServiceStatusStore(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    store.save_results(
        [
            ServiceProbeResult("a", "A", "http://a", ServiceStatus.offline, 1),
            ServiceProbeResult("a", "A", "http://a", ServiceStatus.online, 2),
            ServiceProbeResult("b", "B", "http://b", ServiceStatus.blocked, 1, skipped=True),
        ]
    )

    summary = store.summary()

    assert summary["total"] == 2
    assert summary["online"] == 1
    assert summary["blocked"] == 1
    assert summary["offline"] == 0


def test_service_status_store_recent_results(tmp_path) -> None:
    store = ServiceStatusStore(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    store.save_results(
        [
            ServiceProbeResult("a", "A", "http://a", ServiceStatus.online, 1),
            ServiceProbeResult("b", "B", "http://b", ServiceStatus.offline, 2, error="no route"),
        ]
    )

    recent = store.recent_results(limit=1)

    assert len(recent) == 1
    assert recent[0]["service_id"] == "b"
    assert recent[0]["error"] == "no route"
