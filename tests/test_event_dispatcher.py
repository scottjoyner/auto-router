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


@pytest.mark.asyncio
async def test_transport_failure_dead_letters_instead_of_retrying_forever(
    monkeypatch, tmp_path
) -> None:
    """An unreachable sink must not leave an immortal row at the head of the queue.

    The attempt cap existed only on the HTTP-response path. A transport failure --
    connection refused, DNS failure, timeout -- hardcoded `retry=True` and never
    reached dead_letter, while an event the sink actively rejects was cleaned up
    after five attempts. That is backwards: the failure most likely to persist was
    the immortal one.

    It matters because `pending()` selects status IN ('pending','retry') ordered
    oldest-first under a LIMIT. Every immortal row therefore holds a slot at the
    head of every batch, and once they fill the limit, newly enqueued events are
    never dispatched at all -- starvation that presents as a quiet outbox rather
    than as a failure.

    Asserted by running the exception path to exhaustion rather than by reading
    the branch, because the bug was precisely that the branch did not do this.
    """
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    outbox.enqueue(
        OutboxEvent(
            event_type="router.service_snapshot.recorded",
            idempotency_key="service:a:1:online",
            payload={"service_id": "a"},
        )
    )

    async def unreachable(self, url, json, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", unreachable)
    dispatcher = AssistXEventDispatcher(
        outbox, sink_url="http://assistx.test/events", max_attempts=3
    )

    seen = []
    for _ in range(5):
        results = await dispatcher.dispatch_pending()
        seen.append(results[0].status)
        # Nothing new to dispatch once it is terminal; stop early.
        if outbox.pending(limit=10) == []:
            break

    assert "dead_letter" in seen, f"transport failure never became terminal: {seen}"
    assert outbox.summary()["dead_letter"] == 1
    assert outbox.summary()["retry"] == 0, (
        "a dead-lettered event must leave the retry set, or it keeps consuming a "
        "slot at the head of every batch"
    )
    assert outbox.pending(limit=10) == [], "a dead-lettered event is still pending"
