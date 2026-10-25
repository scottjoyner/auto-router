"""Synthetic-only AssistX event-dispatch failure-custody redaction tests.

Loads the real event_outbox.py and event_dispatcher.py without fleet bootstrap.
The canonical contracts import is stubbed with only a schema version; all
persistence, retry logic and HTTP-error handling under test is the real code.
Never calls the real AssistX sink or an inference endpoint.
"""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import patch

import httpx
import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "auto_router"
CANARY = "SYNTHETIC_FAKE_COOKIE_AND_TOKEN_DO_NOT_LOG"


def load_without_runtime():
    fake_package = types.ModuleType("auto_router")
    fake_package.__path__ = [str(SOURCE)]
    shim = types.ModuleType("auto_router.contracts_shim")
    shim.SCHEMA_VERSION = "test-fixture-only"
    loaded = {}
    with patch.dict(sys.modules, {
        "auto_router": fake_package,
        "auto_router.contracts_shim": shim,
    }):
        for name in ("event_outbox", "event_dispatcher"):
            spec = importlib.util.spec_from_file_location(
                "auto_router." + name, SOURCE / (name + ".py")
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            loaded[name] = module
    return loaded


MODULES = load_without_runtime()
Outbox = MODULES["event_outbox"].EventOutbox
Event = MODULES["event_outbox"].OutboxEvent
Dispatcher = MODULES["event_dispatcher"].AssistXEventDispatcher


def make_outbox(tmp_path):
    outbox = Outbox(f"sqlite:///{tmp_path / 'synthetic-event.sqlite3'}")
    eid = outbox.enqueue(Event(
        event_type="router.synthetic.failure",
        idempotency_key="synthetic-leak-replay-1",
        payload={"synthetic": True},
    ))
    return outbox, eid


@pytest.mark.parametrize("status,expected", [
    (400, "dead_letter"), (401, "dead_letter"), (403, "dead_letter"),
    (429, "retry"), (500, "retry"), (503, "retry"),
])
def test_http_error_redacted_in_memory_and_persisted_outbox(
    monkeypatch, tmp_path, status, expected,
):
    outbox, eid = make_outbox(tmp_path)

    async def fake_post(self, url, json, **kwargs):
        return httpx.Response(
            status,
            text="malicious upstream error includes secret " + CANARY,
            headers={"Set-Cookie": CANARY, "x-extra-private": CANARY},
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    disp = Dispatcher(outbox, sink_url="http://synthetic.invalid/api/events")
    results = asyncio.run(disp.dispatch_pending())
    assert len(results) == 1
    result = results[0]
    assert result.event_id == eid
    assert result.status == expected
    assert result.retry == (expected == "retry")
    assert result.status_code == status
    assert result.error == f"AssistX returned HTTP {status}"
    assert CANARY not in str(result.to_dict())
    persisted = outbox.recent(limit=2)[0]
    assert persisted["last_error"] == f"AssistX returned HTTP {status}"
    assert persisted["status"] == expected
    assert CANARY not in str(persisted)


@pytest.mark.parametrize("error,max_attempts", [
    (httpx.ConnectError("synthetic URL echoed " + CANARY), 3),
    (httpx.ReadTimeout("synthetic bearer echo " + CANARY), 1),
    (RuntimeError("transport unexpectedly echoed " + CANARY), 2),
])
def test_transport_exception_redacted_and_attempt_cap_preserved(
    monkeypatch, tmp_path, error, max_attempts,
):
    outbox, _ = make_outbox(tmp_path)

    async def failed(self, url, json, **kwargs):
        raise error

    monkeypatch.setattr(httpx.AsyncClient, "post", failed)
    disp = Dispatcher(
        outbox, sink_url="http://synthetic.invalid/api/events",
        max_attempts=max_attempts,
    )

    for attempt in range(max_attempts):
        result = asyncio.run(disp.dispatch_pending())[0]
        expect = "dead_letter" if attempt + 1 == max_attempts else "retry"
        assert result.status == expect
        assert result.error == "AssistX event delivery transport failure"
        assert CANARY not in str(result.to_dict())
        record = outbox.recent(limit=1)[0]
        assert record["last_error"] == result.error
        assert CANARY not in str(record)
        assert record["attempts"] == attempt + 1
    assert outbox.pending(limit=1) == []
    assert outbox.summary()["dead_letter"] == 1


def test_idempotent_409_delivery_remains_unchanged(monkeypatch, tmp_path):
    outbox, _ = make_outbox(tmp_path)

    async def duplicate(self, url, json, **kwargs):
        return httpx.Response(
            409, text=CANARY, headers={"Set-Cookie": CANARY},
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", duplicate)
    response = asyncio.run(
        Dispatcher(outbox, sink_url="http://synthetic.invalid/api/events")
        .dispatch_pending()
    )[0]
    assert response.delivered is True
    assert response.status == "delivered"
    assert outbox.summary()["delivered"] == 1
    assert outbox.recent(limit=1)[0]["last_error"] is None
