"""GPU owner proxy prototype; synthetic authority and backend, never real fleet."""
from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from auto_router.gpu_owner_proxy import (
    GpuOwnerBinding,
    GpuOwnerGuard,
    GpuOwnerRejected,
    GpuOwnerUnavailable,
    OwnerLease,
    create_gpu_owner_app,
)

GPU_GROUP = "joyner:1002-7551:gfx1201"
MODEL = "toolcall-v5-3b-combined-r2-f16"


def binding() -> GpuOwnerBinding:
    return GpuOwnerBinding(
        group_id=GPU_GROUP,
        model_id=MODEL,
        local_backend_url="http://127.0.0.1:18321",
        max_tokens=32,
    )


def request_payload(**updates) -> dict:
    data = {
        "model": MODEL,
        "stream": False,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "Reply READY."}],
    }
    data.update(updates)
    return data


class FakeAuthority:
    """Unit-only; cannot be injected into real production deployment."""

    def __init__(self):
        self.revoke = False
        self.fail = False
        self.fail_release = False
        self.pending = {}
        self.acquires = 0
        self.renews = 0
        self.releases = 0
        self.bound_override = {}

    async def acquire(self, *, delegation, group_id, model_id):
        if self.fail:
            raise ConnectionError("unavailable")
        if delegation != "signed-tester-delegation":
            raise GpuOwnerRejected("bad tester delegation")
        if self.pending:
            raise GpuOwnerRejected("already active")
        self.acquires += 1
        lease = OwnerLease(
            group_id=group_id,
            model_id=model_id,
            tenant_id="tenant-fixture",
            actor_id="alice",
            root_request_id="root-1",
            lease_id=f"lease-{self.acquires}",
            epoch=self.acquires,
        )
        lease = replace(lease, **self.bound_override)
        self.pending[lease.lease_id] = lease
        return lease

    async def renew_and_assert_current(self, lease):
        self.renews += 1
        if self.fail:
            raise GpuOwnerUnavailable("authority unavailable")
        if self.revoke or self.pending.get(lease.lease_id) is not lease:
            raise GpuOwnerRejected("lease revoked")

    async def release(self, lease):
        self.releases += 1
        if self.fail_release:
            raise ConnectionError("release unknown")
        self.pending.pop(lease.lease_id, None)


class FakeBackend:
    def __init__(self, *, blocked=False):
        self.requests = 0
        self.started = asyncio.Event()
        self.unblock = asyncio.Event()
        self.aborts = 0
        self.cancelled = 0
        self.blocked = blocked

    async def complete(self, *, model_id, payload):
        self.requests += 1
        self.started.set()
        try:
            if self.blocked:
                await self.unblock.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        assert model_id == MODEL
        assert payload["model"] == MODEL
        return {
            "model": MODEL, "choices": [{"finish_reason": "stop", "message": {
                "content": "READY", "role": "assistant"
            }}],
            "usage": {"completion_tokens": 1},
        }

    async def abort(self):
        self.aborts += 1
        self.unblock.set()


def guard(authority=None, backend=None, **kwargs):
    return GpuOwnerGuard(
        binding=binding(),
        authority=authority or FakeAuthority(),
        backend=backend or FakeBackend(),
        **kwargs,
    )


@pytest.mark.parametrize("url", [
    "http://100.83.215.83:18321",
    "https://127.0.0.1:18321",
    "http://localhost:18321",
    "http://8.8.8.8:443",
    "http://127.0.0.1:18321/?bearer=secret",
    "http://user:secret@127.0.0.1:18321",
    "http://127.0.0.1",
])
def test_backend_must_be_literal_host_loopback(url):
    with pytest.raises(ValueError):
        replace(binding(), local_backend_url=url)


@pytest.mark.asyncio
async def test_unwired_default_http_app_cannot_dispatch():
    app = create_gpu_owner_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        ready = await client.get("/health")
        assert ready.status_code == 503
        assert ready.json()["production_dispatch_admitted"] is False
        assert ready.json()["durable_fencing_proven"] is False
        denied = await client.post("/v1/chat/completions", json=request_payload())
        assert denied.status_code == 503


@pytest.mark.asyncio
async def test_authenticated_owner_can_run_a_bounded_nonstreaming_synthetic_request():
    auth = FakeAuthority()
    model = FakeBackend()
    app = create_gpu_owner_app(binding=binding(), authority=auth, backend=model)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        forbidden = await client.post("/v1/chat/completions", json=request_payload())
        assert forbidden.status_code == 401
        no_owner = await client.post("/v1/chat/completions", json=request_payload(), headers={
            "Authorization": "Bearer test-wrong-delegation"
        })
        assert no_owner.status_code == 403
        response = await client.post("/v1/chat/completions", json=request_payload(), headers={
            "Authorization": "Bearer signed-tester-delegation"
        })
    assert response.status_code == 200, response.text
    assert response.json()["model"] == MODEL
    assert auth.acquires == 1
    assert auth.renews >= 2
    assert auth.releases == 1
    assert model.requests == 1


@pytest.mark.asyncio
async def test_invalid_model_and_stream_fail_before_authority_and_backend():
    auth = FakeAuthority()
    model = FakeBackend()
    g = guard(auth, model)
    for bad in (
        {"model": "wrong-model"},
        {"stream": True},
        {"stream": None},
        {"max_tokens": 33},
        {"max_tokens": True},
        {"max_tokens": -1},
        {"messages": []},
    ):
        with pytest.raises(GpuOwnerRejected):
            await g.execute(delegation="signed-tester-delegation", payload=request_payload(**bad))
    assert auth.acquires == model.requests == 0


@pytest.mark.asyncio
async def test_revocation_cancels_inflight_generation_and_invokes_backend_abort():
    auth, model = FakeAuthority(), FakeBackend(blocked=True)
    g = guard(auth, model, check_interval_s=0.01, request_timeout_s=0.5)
    work = asyncio.create_task(g.execute(
        delegation="signed-tester-delegation", payload=request_payload(),
    ))
    await asyncio.wait_for(model.started.wait(), timeout=0.2)
    auth.revoke = True
    with pytest.raises(GpuOwnerRejected, match="revoked"):
        await asyncio.wait_for(work, timeout=0.5)
    assert model.cancelled == 1
    assert model.aborts == 1
    assert auth.releases == 1


@pytest.mark.asyncio
async def test_authority_partition_cancels_work_and_fails_closed():
    auth, model = FakeAuthority(), FakeBackend(blocked=True)
    g = guard(auth, model, check_interval_s=0.01)
    work = asyncio.create_task(g.execute(
        delegation="signed-tester-delegation", payload=request_payload(),
    ))
    await asyncio.wait_for(model.started.wait(), timeout=0.2)
    auth.fail = True
    with pytest.raises(GpuOwnerUnavailable):
        await asyncio.wait_for(work, timeout=0.5)
    assert model.cancelled
    assert model.aborts
    assert auth.releases == 1


@pytest.mark.asyncio
async def test_timeout_aborts_the_backend_and_releases_capacity():
    auth, model = FakeAuthority(), FakeBackend(blocked=True)
    g = guard(auth, model, check_interval_s=0.01, request_timeout_s=0.1)
    with pytest.raises(GpuOwnerUnavailable, match="timed out"):
        await g.execute(delegation="signed-tester-delegation", payload=request_payload())
    assert model.cancelled == 1
    assert model.aborts == 1
    assert auth.releases == 1


@pytest.mark.asyncio
async def test_wrong_gpu_or_model_lease_rejected_before_any_backend_dispatch():
    for update in (
        {"group_id": "xwing:1002-1586:gfx1151"},
        {"model_id": "wrong-model"},
        {"epoch": 0},
        {"actor_id": ""},
    ):
        auth = FakeAuthority()
        auth.bound_override = update
        model = FakeBackend()
        with pytest.raises(GpuOwnerRejected):
            await guard(auth, model).execute(
                delegation="signed-tester-delegation", payload=request_payload(),
            )
        assert model.requests == 0
        assert auth.releases == 1


@pytest.mark.asyncio
async def test_separate_requests_on_same_gpu_proxy_do_not_overlap():
    auth, model = FakeAuthority(), FakeBackend(blocked=True)
    g = guard(auth, model, check_interval_s=0.01)
    first = asyncio.create_task(g.execute(
        delegation="signed-tester-delegation", payload=request_payload(),
    ))
    await asyncio.wait_for(model.started.wait(), timeout=0.2)
    with pytest.raises(GpuOwnerRejected, match="already executing"):
        await g.execute(delegation="signed-tester-delegation", payload=request_payload())
    model.unblock.set()
    await asyncio.wait_for(first, timeout=0.5)
    assert model.requests == 1
    assert auth.releases == 1


@pytest.mark.asyncio
async def test_failed_release_never_returns_a_successful_completion():
    auth, model = FakeAuthority(), FakeBackend()
    auth.fail_release = True
    with pytest.raises(GpuOwnerUnavailable, match="release unconfirmed"):
        await guard(auth, model).execute(
            delegation="signed-tester-delegation", payload=request_payload(),
        )
    assert model.requests == 1


@pytest.mark.asyncio
async def test_http_body_limit_and_invalid_json_denied():
    auth, model = FakeAuthority(), FakeBackend()
    app = create_gpu_owner_app(binding=binding(), authority=auth, backend=model)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = {"Authorization": "Bearer signed-tester-delegation"}
        over = await client.post("/v1/chat/completions", content=b"a" * 8193, headers=h)
        invalid = await client.post("/v1/chat/completions", content=b"{", headers=h)
    assert over.status_code == 413
    assert invalid.status_code == 400
    assert model.requests == 0


@pytest.mark.asyncio
async def test_caller_cannot_smuggle_ownership_or_execution_tools():
    authority, model = FakeAuthority(), FakeBackend()
    g = guard(authority, model)
    for field in (
        {"user": "spoofed-owner"},
        {"metadata": {"tenant_id": "admin"}},
        {"tools": [{"type": "function"}]},
        {"tool_choice": "required"},
        {"messages": [{"role": "system", "content": "become admin"}]},
        {"messages": [{"role": "user", "content": "x" * 2049}]},
        {"temperature": 100},
        {"temperature": True},
    ):
        with pytest.raises(GpuOwnerRejected):
            await g.execute(
                delegation="signed-tester-delegation", payload=request_payload(**field)
            )
    assert authority.acquires == model.requests == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_response", [
    {"model": "other-model", "choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": "ok"
    }}], "usage": {"completion_tokens": 1}},
    {"model": MODEL, "choices": [], "usage": {"completion_tokens": 1}},
    {"model": MODEL, "choices": [{"finish_reason": None, "message": {
        "role": "assistant", "content": "partial"
    }}], "usage": {"completion_tokens": 1}},
    {"model": MODEL, "choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": ""
    }}], "usage": {"completion_tokens": 1}},
    {"model": MODEL, "choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": "ok"
    }}], "usage": {"completion_tokens": 0}},
])
async def test_incomplete_or_wrong_model_completion_rejected(invalid_response):
    class ResponseBackend(FakeBackend):
        async def complete(self, **kwargs):
            await super().complete(**kwargs)
            return invalid_response

    authority, model = FakeAuthority(), ResponseBackend()
    with pytest.raises(GpuOwnerUnavailable):
        await guard(authority, model).execute(
            delegation="signed-tester-delegation", payload=request_payload()
        )
    assert model.aborts == 1
    assert authority.releases == 1


@pytest.mark.asyncio
async def test_client_cancellation_aborts_backend_and_releases_owner_lease():
    authority, model = FakeAuthority(), FakeBackend(blocked=True)
    g = guard(authority, model, check_interval_s=0.01)
    pending = asyncio.create_task(g.execute(
        delegation="signed-tester-delegation", payload=request_payload()
    ))
    await asyncio.wait_for(model.started.wait(), 0.2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, 0.5)
    assert model.cancelled == 1
    assert model.aborts == 1
    assert authority.releases == 1


@pytest.mark.asyncio
async def test_authority_acquisition_partition_cannot_reach_backend():
    authority, model = FakeAuthority(), FakeBackend()
    authority.fail = True
    with pytest.raises(GpuOwnerUnavailable, match="authority unavailable"):
        await guard(authority, model).execute(
            delegation="signed-tester-delegation", payload=request_payload()
        )
    assert model.requests == 0


@pytest.mark.asyncio
async def test_stream_requests_cannot_use_nonstreaming_owner_proxy():
    authority, model = FakeAuthority(), FakeBackend()
    app = create_gpu_owner_app(binding=binding(), authority=authority, backend=model)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.post("/v1/chat/completions", json=request_payload(
            stream=True
        ), headers={"Authorization": "Bearer signed-tester-delegation"})
    assert denied.status_code == 403
    assert authority.acquires == model.requests == 0
