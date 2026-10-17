"""Isolated 2-router reservation tests; NO production Redis or GPU dispatch."""
from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict

import pytest

from auto_router.gpu_distributed_lease import (
    GpuLeaseOccupied,
    GpuLeaseUnavailable,
    RedisGpuReservation,
    remote_dispatch_is_fenced,
)


class MemoryRedis:
    """Atomic in-process Lua simulator; real Redis checked separately."""

    def __init__(self):
        self.data = {}
        self.counters = defaultdict(int)
        self.expirations = {}
        self.lock = asyncio.Lock()
        self.fail = False

    def _current(self, key):
        if self.expirations.get(key, float("inf")) <= time.monotonic():
            self.data.pop(key, None)
            self.expirations.pop(key, None)
        return self.data.get(key)

    async def eval(self, script, key_count, *args):
        if self.fail:
            raise ConnectionError("synthetic network partition")
        async with self.lock:
            if "gpu_lease_acquire_v1" in script:
                assert key_count == 2
                lock_key, seq_key, owner, ttl = args
                if self._current(lock_key) is not None:
                    return [0, 0]
                self.counters[seq_key] += 1
                seq = self.counters[seq_key]
                self.data[lock_key] = f"{owner}:{seq}"
                self.expirations[lock_key] = time.monotonic() + ttl / 1000
                return [1, seq]
            assert key_count == 1
            key, owner, *tail = args
            if self._current(key) != owner:
                return 0
            if "gpu_lease_renew_v1" in script:
                self.expirations[key] = time.monotonic() + tail[0] / 1000
                return 1
            if "gpu_lease_release_v1" in script:
                self.data.pop(key, None)
                self.expirations.pop(key, None)
                return 1
            raise ValueError("unexpected script")

    async def get(self, key):
        if self.fail:
            raise ConnectionError("synthetic network partition")
        val = self._current(key)
        return val.encode() if val is not None else None


@pytest.mark.asyncio
async def test_competing_router_clients_only_one_gets_the_card():
    fake = MemoryRedis()
    clients = [RedisGpuReservation(fake) for _ in range(16)]
    async def reserve(client):
        try:
            return await client.reserve("joyner:1002-7551:gfx1201")
        except GpuLeaseOccupied:
            return None

    answers = await asyncio.gather(*(reserve(c) for c in clients))
    winners = [x for x in answers if x is not None]
    assert len(winners) == 1
    winner = winners[0]
    assert await clients[0].renew(winner)
    await clients[0].assert_held(winner)
    assert await clients[0].release(winner)
    assert not await clients[0].release(winner)


@pytest.mark.asyncio
async def test_expired_old_owner_cannot_release_or_renew_the_new_owner():
    fake = MemoryRedis()
    a, b = RedisGpuReservation(fake), RedisGpuReservation(fake)
    first = await a.reserve("xwing:1002-1586:gfx1151", ttl_ms=100)
    await asyncio.sleep(0.125)
    second = await b.reserve("xwing:1002-1586:gfx1151", ttl_ms=1000)
    assert second.sequence > first.sequence
    assert not await a.release(first)
    assert not await a.renew(first)
    with pytest.raises(GpuLeaseUnavailable, match="no longer held"):
        await a.assert_held(first)
    await b.assert_held(second)
    assert await b.release(second)


@pytest.mark.asyncio
async def test_network_partition_fails_closed_for_every_lease_operation():
    fake = MemoryRedis()
    manager = RedisGpuReservation(fake)
    lease = await manager.reserve("joyner:1002-7551:gfx1201")
    fake.fail = True
    for request in (
        manager.reserve("xwing:1002-1586:gfx1151"),
        manager.renew(lease),
        manager.release(lease),
        manager.assert_held(lease),
    ):
        with pytest.raises(GpuLeaseUnavailable):
            await request


@pytest.mark.asyncio
async def test_non_gpu_group_names_cannot_trick_the_slot_accounting():
    manager = RedisGpuReservation(MemoryRedis())
    for name in ("", "   ", "X" * 201):
        with pytest.raises(ValueError):
            await manager.reserve(name)
    for ms in (1, 0, 60001):
        with pytest.raises(ValueError):
            await manager.reserve("xwing", ttl_ms=ms)


def test_reservation_is_not_raw_llamacpp_fencing():
    assert not remote_dispatch_is_fenced(backend_enforces_token=False)
    # A caller's optimistic Boolean can never promote raw llama.cpp.
    assert not remote_dispatch_is_fenced(backend_enforces_token=True)


@pytest.mark.asyncio
async def test_lease_expiry_demonstrates_stale_execution_window():
    """Shows why a raw GPU server must verify fence tokens itself."""
    fake = MemoryRedis()
    a, b = RedisGpuReservation(fake), RedisGpuReservation(fake)
    stale = await a.reserve("joyner:1002-7551:gfx1201", ttl_ms=100)
    await a.assert_held(stale)
    # After last router-side check, a stalled worker could still POST to the
    # GPU while B has a valid new reservation. The coordinator cannot revoke
    # that request from a raw llama.cpp HTTP listener.
    await asyncio.sleep(0.125)
    fresh = await b.reserve("joyner:1002-7551:gfx1201", ttl_ms=1000)
    assert fresh.sequence > stale.sequence
    with pytest.raises(GpuLeaseUnavailable):
        await a.assert_held(stale)
    assert not remote_dispatch_is_fenced(backend_enforces_token=False)


@pytest.mark.asyncio
async def test_two_distinct_cards_do_not_conflict():
    fake = MemoryRedis()
    manager = RedisGpuReservation(fake)
    j, x = await asyncio.gather(
        manager.reserve("joyner:1002-7551:gfx1201"),
        manager.reserve("xwing:1002-1586:gfx1151"),
    )
    assert j.group_id != x.group_id
    assert await manager.release(j)
    assert await manager.release(x)


@pytest.mark.asyncio
async def test_real_ephemeral_redis_if_test_url_is_explicit():
    url = os.environ.get("GPU_TEST_REDIS_URL")
    if not url:
        pytest.skip("no explicitly isolated disposable Redis test URL")
    assert url == "redis://127.0.0.1:16389/0", "refuse live/shared Redis"
    from redis import asyncio as aioredis

    a = aioredis.from_url(url, decode_responses=False)
    b = aioredis.from_url(url, decode_responses=False)
    prefix = "gpu-isolated-test"
    one = RedisGpuReservation(a, namespace=prefix)
    two = RedisGpuReservation(b, namespace=prefix)
    try:
        group = "joyner:1002-7551:gfx1201"
        first = await one.reserve(group, ttl_ms=1000)
        with pytest.raises(GpuLeaseOccupied):
            await two.reserve(group)
        assert await a.get(one._keys(group)[0]) == first.record.encode()
        assert await two.renew(first)
        assert await one.release(first)
        again = await two.reserve(group, ttl_ms=1000)
        assert again.sequence > first.sequence
        assert not await one.release(first)
        await two.assert_held(again)
        assert await two.release(again)
    finally:
        await a.aclose()
        await b.aclose()


@pytest.mark.asyncio
async def test_two_independent_python_processes_share_real_redis_reservation():
    """An isolated child process reserves the physical card; parent must lose."""
    import selectors
    import subprocess
    import sys
    from pathlib import Path

    url = os.environ.get("GPU_TEST_REDIS_URL")
    if not url:
        pytest.skip("requires disposable local Redis for cross-process test")
    assert url == "redis://127.0.0.1:16389/0"
    from redis import asyncio as aioredis

    worker = r'''
import asyncio
import sys
from redis import asyncio as aioredis
from auto_router.gpu_distributed_lease import RedisGpuReservation
async def main():
    client = aioredis.from_url("redis://127.0.0.1:16389/0")
    lease = await RedisGpuReservation(
        client, namespace="gpu-isolated-two-processes"
    ).reserve("joyner:1002-7551:gfx1201", ttl_ms=5000)
    print("ACQUIRED", flush=True)
    await asyncio.to_thread(sys.stdin.readline)
    print("RELEASED" if await RedisGpuReservation(
        client, namespace="gpu-isolated-two-processes"
    ).release(lease) else "LOST", flush=True)
    await client.aclose()
asyncio.run(main())
'''
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", worker],
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")
            + os.pathsep + os.environ.get("PYTHONPATH", ""),
        },
    )
    client = aioredis.from_url(url)
    mgr = RedisGpuReservation(client, namespace="gpu-isolated-two-processes")
    try:
        assert child.stdout is not None
        ready = selectors.DefaultSelector()
        ready.register(child.stdout, selectors.EVENT_READ)
        assert ready.select(timeout=5), "child failed to acquire in time"
        assert child.stdout.readline().strip() == "ACQUIRED"
        with pytest.raises(GpuLeaseOccupied):
            await mgr.reserve("joyner:1002-7551:gfx1201", ttl_ms=5000)
        assert child.stdin is not None
        child.stdin.write("\n")
        child.stdin.flush()
        out, err = child.communicate(timeout=5)
        assert child.returncode == 0, err
        assert out.strip() == "RELEASED"
        lease = await mgr.reserve("joyner:1002-7551:gfx1201", ttl_ms=1000)
        assert await mgr.release(lease)
    finally:
        await client.aclose()
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


@pytest.mark.asyncio
async def test_nonpersistent_redis_restart_cannot_supply_trusted_fencing_epoch():
    """Sequence reuse after Redis state loss demonstrates unresolved NO-GO."""
    fake = MemoryRedis()
    manager = RedisGpuReservation(fake)
    stale = await manager.reserve("joyner:1002-7551:gfx1201", ttl_ms=3000)
    fake.data.clear()
    fake.counters.clear()
    fake.expirations.clear()
    fresh = await manager.reserve("joyner:1002-7551:gfx1201", ttl_ms=3000)
    assert stale.sequence == fresh.sequence == 1
    assert stale.owner_id != fresh.owner_id
    assert not remote_dispatch_is_fenced(backend_enforces_token=False)
    with pytest.raises(GpuLeaseUnavailable, match="no longer held"):
        await manager.assert_held(stale)
    assert await manager.release(fresh)
