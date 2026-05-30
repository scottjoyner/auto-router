from auto_router.event_outbox import EventOutbox, OutboxEvent


def test_event_outbox_enqueues_idempotently(tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    event = OutboxEvent(
        event_type="router.service_snapshot.recorded",
        idempotency_key="service:a:1:online",
        payload={"service_id": "a", "status": "online"},
    )

    first = outbox.enqueue(event)
    second = outbox.enqueue(event)

    assert first == second
    assert outbox.summary()["pending"] == 1
    assert len(outbox.pending()) == 1


def test_event_outbox_status_transitions(tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    event_id = outbox.enqueue(
        OutboxEvent(
            event_type="router.service_snapshot.recorded",
            idempotency_key="service:a:1:online",
            payload={"service_id": "a", "status": "online"},
        )
    )

    outbox.mark_failed(event_id, "temporary", retry=True)
    assert outbox.summary()["retry"] == 1
    assert outbox.pending()[0]["attempts"] == 1

    outbox.mark_delivered(event_id)
    summary = outbox.summary()
    assert summary["delivered"] == 1
    assert summary["retry"] == 0


def test_event_outbox_dead_letter(tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    event_id = outbox.enqueue(
        OutboxEvent(
            event_type="router.service_snapshot.recorded",
            idempotency_key="service:a:1:offline",
            payload={"service_id": "a", "status": "offline"},
        )
    )

    outbox.mark_failed(event_id, "terminal", retry=False)

    assert outbox.summary()["dead_letter"] == 1
    assert outbox.recent()[0]["last_error"] == "terminal"
