from types import SimpleNamespace

from auto_router.context import ContextSignal, ContextSnapshot
from auto_router.models import Priority, RouterRequest
from auto_router.signal_registry import ContextSignalStore, route_execution_signals, signal_snapshot


def test_route_execution_transient_failure_is_avoid_not_blocked() -> None:
    request = RouterRequest(request_id="req-fail", route="chat_completions", model="auto/fast", priority=Priority.interactive)

    signals = route_execution_signals(
        request=request,
        provider="lmstudio-x1-370",
        model="local/reasoning-large",
        stage="final",
        status_code=503,
        latency_ms=100,
        error=RuntimeError("temporary upstream failure"),
        node_id="x1-370",
    )

    assert {signal.signal_type for signal in signals} == {"avoid"}
    assert all(not signal.is_blocking for signal in signals)


def test_context_signal_store_filters_expired_latest_signals(tmp_path) -> None:
    store = ContextSignalStore(f"sqlite:///{tmp_path / 'signals.sqlite3'}")
    expired = ContextSignal(
        signal_id="route.old.provider",
        target_type="provider",
        target_id="lmstudio-x1-370",
        signal_type="preferred",
        source="route_decision",
        observed_at=1,
        expires_at=2,
    )
    active = ContextSignal(
        signal_id="route.active.provider",
        target_type="provider",
        target_id="lmstudio-x1-370",
        signal_type="preferred",
        source="route_decision",
    )

    store.save_snapshot(signal_snapshot([expired, active], revision="unit-test", source="unit-test"))

    latest_ids = {signal.signal_id for signal in store.latest_signals()}
    assert "route.active.provider" in latest_ids
    assert "route.old.provider" not in latest_ids


def test_context_signal_store_prunes_old_events_by_retention(tmp_path) -> None:
    store = ContextSignalStore(f"sqlite:///{tmp_path / 'signals.sqlite3'}")
    old = ContextSignal(
        signal_id="provider.old.health",
        target_type="provider",
        target_id="old",
        signal_type="avoid",
        source="provider_health",
        observed_at=1,
    )
    current = ContextSignal(
        signal_id="provider.current.health",
        target_type="provider",
        target_id="current",
        signal_type="preferred",
        source="provider_health",
    )

    store.save_snapshot(ContextSnapshot(revision="old", source="unit-test", signals=[old]))
    store.prune(retention_seconds=1)
    store.save_snapshot(ContextSnapshot(revision="current", source="unit-test", signals=[current]))

    latest_ids = {signal.signal_id for signal in store.latest_signals()}
    assert "provider.current.health" in latest_ids
    assert "provider.old.health" not in latest_ids
