import httpx
import pytest

from auto_router.event_dispatcher import AssistXEventDispatcher
from auto_router.event_outbox import EventOutbox, OutboxEvent


@pytest.mark.asyncio
async def test_dispatcher_dry_run_does_not_change_outbox(tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    event_id = outbox.enqueue(
        OutboxEvent(
            event_type="router.service_snapshot.recorded",
            idempotency_key="service:a:1:online",
            payload={"service_id": "a"},
        )
    )
    dispatcher = AssistXEventDispatcher(outbox, sink_url="http://assistx.test/events")

    results = await dispatcher.dispatch_pending(dry_run=True)

    assert results[0].event_id == event_id
    assert results[0].status == "dry_run"
    assert outbox.summary()["pending"] == 1


@pytest.mark.asyncio
async def test_dispatcher_without_sink_reports_not_configured(tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    outbox.enqueue(
        OutboxEvent(
            event_type="router.service_snapshot.recorded",
            idempotency_key="service:a:1:online",
            payload={"service_id": "a"},
        )
    )
    dispatcher = AssistXEventDispatcher(outbox, sink_url=None)

    results = await dispatcher.dispatch_pending()

    assert results[0].status == "not_configured"
    assert outbox.summary()["pending"] == 1


@pytest.mark.asyncio
async def test_dispatcher_marks_success_delivered(monkeypatch, tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    outbox.enqueue(
        OutboxEvent(
            event_type="router.agent_cli.discovered",
            idempotency_key="cli:x:gemini:1:true:true",
            payload={"name": "gemini-cli"},
        )
    )

    async def fake_post(self, url, json, **kwargs):
        return httpx.Response(202, json={"ok": True})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    dispatcher = AssistXEventDispatcher(outbox, sink_url="http://assistx.test/events")

    results = await dispatcher.dispatch_pending()

    assert results[0].status == "delivered"
    assert outbox.summary()["delivered"] == 1


@pytest.mark.asyncio
async def test_dispatcher_marks_409_delivered(monkeypatch, tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    outbox.enqueue(
        OutboxEvent(
            event_type="router.service_snapshot.recorded",
            idempotency_key="service:a:1:online",
            payload={"service_id": "a"},
        )
    )

    async def fake_post(self, url, json, **kwargs):
        return httpx.Response(409, text="duplicate")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    dispatcher = AssistXEventDispatcher(outbox, sink_url="http://assistx.test/events")

    results = await dispatcher.dispatch_pending()

    assert results[0].status == "delivered"
    assert outbox.summary()["delivered"] == 1


@pytest.mark.asyncio
async def test_dispatcher_canonical_envelope_uses_string_node_id_fallback(monkeypatch, tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    outbox.enqueue(
        OutboxEvent(
            event_type="router.route_decision",
            idempotency_key="router.route_decision:req-1:draft:cerebras:gpt-oss-120b",
            payload={"provider_id": "cerebras", "provider": "cerebras", "node_id": None},
        )
    )

    captured = {}

    async def fake_post(self, url, json, **kwargs):
        captured["json"] = json
        return httpx.Response(202, json={"ok": True})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    dispatcher = AssistXEventDispatcher(outbox, sink_url="http://assistx.test/events")

    results = await dispatcher.dispatch_pending()

    assert results[0].status == "delivered"
    assert captured["json"]["node_id"] == "cerebras"


@pytest.mark.asyncio
async def test_dispatcher_retries_transient_error(monkeypatch, tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    outbox.enqueue(
        OutboxEvent(
            event_type="router.service_snapshot.recorded",
            idempotency_key="service:a:1:online",
            payload={"service_id": "a"},
        )
    )

    async def fake_post(self, url, json, **kwargs):
        return httpx.Response(503, text="temporarily down")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    dispatcher = AssistXEventDispatcher(outbox, sink_url="http://assistx.test/events")

    results = await dispatcher.dispatch_pending()

    assert results[0].status == "retry"
    assert outbox.summary()["retry"] == 1


@pytest.mark.asyncio
async def test_dispatcher_dead_letters_after_max_attempts(monkeypatch, tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    event_id = outbox.enqueue(
        OutboxEvent(
            event_type="router.service_snapshot.recorded",
            idempotency_key="service:a:1:online",
            payload={"service_id": "a"},
        )
    )
    outbox.mark_failed(event_id, "old failure", retry=True)

    async def fake_post(self, url, json, **kwargs):
        return httpx.Response(503, text="still down")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    dispatcher = AssistXEventDispatcher(outbox, sink_url="http://assistx.test/events", max_attempts=2)

    results = await dispatcher.dispatch_pending()

    assert results[0].status == "dead_letter"
    assert outbox.summary()["dead_letter"] == 1
