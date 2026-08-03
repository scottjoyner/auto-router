from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from auto_router import claim_fence
from auto_router.claim_fence import ExecutorClaimFenceError, assert_executor_claim_current


class _Projection:
    generation = 9

    @staticmethod
    def is_fresh() -> bool:
        return True


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    response = None
    observed = {}

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, headers=None):
        type(self).observed = {"url": url, "headers": headers}
        return type(self).response


def _request(**metadata):
    return SimpleNamespace(metadata=metadata)


def _state():
    return SimpleNamespace(
        runtime_projection_manager=SimpleNamespace(current=_Projection())
    )


def _active_payload(**overrides):
    payload = {
        "active": True,
        "task_id": "task-1",
        "claim_id": "claim-1",
        "agent_id": "hermes-test",
        "projection_generation": 9,
        "lease_expires_at_ts": int(time.time() * 1000) + 60_000,
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv(
        "AUTO_ROUTER_EXECUTOR_CLAIM_STATUS_URL",
        "http://assistx.test",
    )
    monkeypatch.setenv(
        "AUTO_ROUTER_ASSISTX_EXECUTOR_SERVICE_TOKEN",
        "router-claim-secret",
    )
    monkeypatch.setattr(claim_fence.httpx, "AsyncClient", _Client)
    _Client.observed = {}


@pytest.mark.asyncio
async def test_active_claim_passes_and_uses_service_credential():
    _Client.response = _Response(200, _active_payload())
    await assert_executor_claim_current(
        _request(
            assistx_executor={
                "task_id": "task-1",
                "claim_id": "claim-1",
                "agent_id": "hermes-test",
                "projection_generation": 9,
            }
        ),
        _state(),
    )
    assert _Client.observed["url"].endswith(
        "/api/executor/claims/task-1/status"
    )
    assert _Client.observed["headers"] == {
        "Authorization": "Bearer router-claim-secret"
    }


@pytest.mark.asyncio
async def test_revoked_or_replaced_claim_fails_closed():
    _Client.response = _Response(200, _active_payload(active=False, reason="lease_expired"))
    with pytest.raises(ExecutorClaimFenceError, match="lease_expired"):
        await assert_executor_claim_current(
            _request(
                assistx_executor={
                    "task_id": "task-1",
                    "claim_id": "claim-1",
                    "agent_id": "hermes-test",
                    "projection_generation": 9,
                }
            ),
            _state(),
        )

    _Client.response = _Response(200, _active_payload(claim_id="claim-2"))
    with pytest.raises(ExecutorClaimFenceError, match="no longer matches"):
        await assert_executor_claim_current(
            _request(
                assistx_executor={
                    "task_id": "task-1",
                    "claim_id": "claim-1",
                    "agent_id": "hermes-test",
                    "projection_generation": 9,
                }
            ),
            _state(),
        )


@pytest.mark.asyncio
async def test_internal_assistx_service_bypasses_task_claim_lookup(monkeypatch):
    monkeypatch.delenv("AUTO_ROUTER_EXECUTOR_CLAIM_STATUS_URL", raising=False)
    monkeypatch.delenv(
        "AUTO_ROUTER_ASSISTX_EXECUTOR_SERVICE_TOKEN",
        raising=False,
    )
    await assert_executor_claim_current(
        _request(assistx_service={"authenticated": True}),
        _state(),
    )
    assert _Client.observed == {}


@pytest.mark.asyncio
async def test_missing_executor_lineage_fails_closed():
    with pytest.raises(ExecutorClaimFenceError, match="lineage is missing"):
        await assert_executor_claim_current(_request(), _state())
