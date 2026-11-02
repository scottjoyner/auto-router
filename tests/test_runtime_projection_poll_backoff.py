"""Read-only polling retry semantics; never substitute availability for admission."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from auto_router import runtime_projection as projection


@pytest.mark.parametrize(
    "statuses,expected_intervals,successes",
    [
        ([503, 503, 503, 503, 503], [10.0, 20.0, 40.0, 60.0, 60.0], 0),
        ([503, 200, 503, 200], [10.0, 5.0, 10.0, 5.0], 2),
        ([503, 401, 503, 429], [10.0, 5.0, 10.0, 5.0], 0),
    ],
)
def test_projection_503_backoff_preserves_auth_and_requires_apply(
    monkeypatch, statuses, expected_intervals, successes
):
    monkeypatch.setenv("AUTO_ROUTER_RUNTIME_PROJECTION_URL", "http://assistx-api:8000/api/router/runtime-projection")
    monkeypatch.setenv("AUTO_ROUTER_RUNTIME_PROJECTION_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("AUTO_ROUTER_RUNTIME_PROJECTION_503_MAX_INTERVAL_SECONDS", "60")
    seen = []
    applied = []
    statuses_iter = iter(statuses)

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url):
            assert url == "http://assistx-api:8000/api/router/runtime-projection"
            status = next(statuses_iter)
            seen.append(status)
            return httpx.Response(status, json={"generation": len(seen)}, request=httpx.Request("GET", url))

    async def fake_sleep(duration):
        intervals.append(duration)
        if len(intervals) == len(expected_intervals):
            raise asyncio.CancelledError()

    async def apply(payload):
        applied.append(payload["generation"])

    intervals = []
    manager = SimpleNamespace(last_error="", last_attempt_at_ms=0, apply=apply)
    monkeypatch.setattr(projection.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(projection.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(projection.projection_poll_task(None, manager))
    assert intervals == expected_intervals
    assert len(applied) == successes
    assert seen == statuses
    if statuses[-1] != 200:
        assert manager.last_error


def test_signature_validation_failure_does_not_reuse_503_retry_streak(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_RUNTIME_PROJECTION_URL", "http://assistx-api:8000/api/router/runtime-projection")
    monkeypatch.setenv("AUTO_ROUTER_RUNTIME_PROJECTION_INTERVAL_SECONDS", "5")
    statuses = iter([503, 200, 503])
    observed_delays = []
    applications = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url):
            status = next(statuses)
            return httpx.Response(status, json={"generation": 5}, request=httpx.Request("GET", url))

    async def reject_unsigned(payload):
        applications.append(payload["generation"])
        raise ValueError("runtime projection Ed25519 signature mismatch")

    async def sleep(seconds):
        observed_delays.append(seconds)
        if len(observed_delays) == 3:
            raise asyncio.CancelledError()

    manager = SimpleNamespace(last_error="", last_attempt_at_ms=0, apply=reject_unsigned)
    monkeypatch.setattr(projection.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(projection.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(projection.projection_poll_task(None, manager))
    assert observed_delays == [10.0, 5.0, 10.0]
    assert applications == [5]
    assert "503" in manager.last_error
