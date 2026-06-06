from auto_router.live_models import LiveModelSnapshot
from auto_router.model_registry import ModelRegistryStore


def test_model_registry_saves_and_restores_latest_snapshot(tmp_path) -> None:
    store = ModelRegistryStore(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    store.save_snapshot(
        LiveModelSnapshot(
            provider="cerebras",
            ok=False,
            fetched_at=1,
            expires_at=60,
            error="temporary",
        )
    )
    store.save_snapshot(
        LiveModelSnapshot(
            provider="cerebras",
            ok=True,
            fetched_at=2,
            expires_at=3602,
            models=[{"id": "gpt-oss-120b"}, {"id": "zai-glm-4.7"}],
        )
    )

    latest = store.latest_for_provider("cerebras")

    assert latest is not None
    assert latest.ok is True
    assert latest.provider == "cerebras"
    assert [model["id"] for model in latest.models] == ["gpt-oss-120b", "zai-glm-4.7"]


def test_model_registry_summary_counts_latest_snapshots(tmp_path) -> None:
    store = ModelRegistryStore(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    store.save_snapshot(
        LiveModelSnapshot(
            provider="cerebras",
            ok=True,
            fetched_at=100,
            expires_at=9999999999,
            models=[{"id": "a"}, {"id": "b"}],
        )
    )
    store.save_snapshot(
        LiveModelSnapshot(
            provider="groq",
            ok=False,
            fetched_at=100,
            expires_at=9999999999,
            error="missing key",
        )
    )

    summary = store.summary()

    assert summary["providers"] == 2
    assert summary["ok"] == 1
    assert summary["error"] == 1
    assert summary["models"] == 2


def test_model_registry_recent_snapshots(tmp_path) -> None:
    store = ModelRegistryStore(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    store.save_snapshot(
        LiveModelSnapshot(provider="a", ok=True, fetched_at=1, expires_at=10, models=[])
    )
    store.save_snapshot(
        LiveModelSnapshot(provider="b", ok=True, fetched_at=2, expires_at=10, models=[])
    )

    recent = store.recent_snapshots(limit=1)

    assert len(recent) == 1
    assert recent[0]["provider"] == "b"


def test_model_registry_records_probe_history_and_health_scores(tmp_path) -> None:
    store = ModelRegistryStore(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    previous = LiveModelSnapshot(provider="cerebras", ok=True, fetched_at=1, expires_at=100, models=[{"id": "model-a"}])
    current = LiveModelSnapshot(provider="cerebras", ok=True, fetched_at=2, expires_at=200, models=[{"id": "model-b"}])

    store.save_snapshot(previous)
    store.save_snapshot(current)
    store.save_probe(current, latency_ms=123, previous_snapshot=previous)

    latest_probe = store.latest_probe_for_provider("cerebras")
    health_reports = store.provider_health_reports()
    probe_summary = store.probe_summary()

    assert latest_probe is not None
    assert latest_probe.drift is True
    assert latest_probe.signature is not None
    assert probe_summary["providers"] == 1
    assert probe_summary["drift"] == 1
    assert probe_summary["healthy"] == 0
    assert health_reports[0]["provider"] == "cerebras"
    assert 0 <= int(health_reports[0]["health_score"]) <= 100
