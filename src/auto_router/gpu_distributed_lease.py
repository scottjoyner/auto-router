"""Review-only Redis physical GPU reservation prototype; NOT dispatch fencing.

An atomic reservation coordinates cooperating router processes, but **cannot**
stop a timed-out/stale process from continuing inference after its lease expires.
The raw llama.cpp backend does not validate a fencing token. This module must
NOT be promoted as production dispatch authorization without a GPU-side fence.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

# Two keys in one Redis hash slot for compatibility with cluster routing.
# Epoch counters require durable independent authority to survive Redis loss:
# this prototype does NOT assert monotonically ordered tokens across restarts.
ACQUIRE_LUA = """
-- gpu_lease_acquire_v1
if redis.call('EXISTS', KEYS[1]) == 1 then
  return {0, 0}
end
local seq = redis.call('INCR', KEYS[2])
redis.call('PSETEX', KEYS[1], tonumber(ARGV[2]), ARGV[1] .. ':' .. seq)
return {1, seq}
"""

RENEW_LUA = """
-- gpu_lease_renew_v1
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
  return 0
end
return redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]))
"""

RELEASE_LUA = """
-- gpu_lease_release_v1
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
  return 0
end
return redis.call('DEL', KEYS[1])
"""


class GpuLeaseUnavailable(RuntimeError):
    """Redis is inaccessible or returned an ambiguous result. Fail closed."""


class GpuLeaseOccupied(RuntimeError):
    """Another cooperating client has the physical GPU reservation."""


@dataclass(frozen=True)
class RedisGpuLease:
    group_id: str
    owner_id: str
    sequence: int
    ttl_ms: int

    @property
    def record(self) -> str:
        return f"{self.owner_id}:{self.sequence}"


class RedisGpuReservation:
    """Atomic reservation for *diagnostics*, not GPU-side dispatch authority."""

    def __init__(self, redis_client: Any, *, namespace: str = "gpu-canary-v1") -> None:
        allowed = "abcdefghijklmnopqrstuvwxyz0123456789-_"
        if not namespace or any(c not in allowed for c in namespace):
            raise ValueError("reservation namespace must be simple and nonempty")
        self.client = redis_client
        self.namespace = namespace

    def _keys(self, group_id: str) -> tuple[str, str]:
        if not group_id or not group_id.strip() or len(group_id) > 200:
            raise ValueError("invalid physical GPU resource identifier")
        digest = hashlib.sha256(group_id.encode()).hexdigest()[:32]
        # {} creates the same Redis Cluster hash slot for both keys.
        root = f"{self.namespace}:{{{digest}}}"
        return root + ":active", root + ":sequence"

    async def reserve(self, group_id: str, *, ttl_ms: int = 3000) -> RedisGpuLease:
        if not 100 <= ttl_ms <= 60000:
            raise ValueError("reservation TTL must be 100–60000 ms")
        owner = uuid.uuid4().hex
        keys = self._keys(group_id)
        try:
            response = await self.client.eval(
                ACQUIRE_LUA, 2, *keys, owner, ttl_ms,
            )
        except Exception as exc:
            raise GpuLeaseUnavailable("GPU reservation authority unavailable") from exc
        if (
            not isinstance(response, (list, tuple))
            or len(response) != 2
            or not isinstance(response[0], int)
            or not isinstance(response[1], int)
        ):
            raise GpuLeaseUnavailable("invalid GPU reservation response")
        if response[0] != 1:
            if response == [0, 0] or response == (0, 0):
                raise GpuLeaseOccupied("physical GPU reservation is occupied")
            raise GpuLeaseUnavailable("ambiguous GPU reservation response")
        if response[1] <= 0:
            raise GpuLeaseUnavailable("invalid GPU reservation sequence")
        return RedisGpuLease(group_id, owner, response[1], ttl_ms)

    async def renew(self, lease: RedisGpuLease) -> bool:
        try:
            response = await self.client.eval(
                RENEW_LUA, 1, self._keys(lease.group_id)[0],
                lease.record, lease.ttl_ms,
            )
        except Exception as exc:
            raise GpuLeaseUnavailable("GPU reservation renewal unavailable") from exc
        if response not in (0, 1):
            raise GpuLeaseUnavailable("ambiguous GPU reservation renewal")
        return response == 1

    async def release(self, lease: RedisGpuLease) -> bool:
        try:
            response = await self.client.eval(
                RELEASE_LUA, 1, self._keys(lease.group_id)[0], lease.record,
            )
        except Exception as exc:
            raise GpuLeaseUnavailable("GPU reservation release unavailable") from exc
        if response not in (0, 1):
            raise GpuLeaseUnavailable("ambiguous GPU reservation release")
        return response == 1

    async def assert_held(self, lease: RedisGpuLease) -> None:
        """Check before dispatch; cannot stop a request after the check."""
        try:
            result = await self.client.get(self._keys(lease.group_id)[0])
        except Exception as exc:
            raise GpuLeaseUnavailable("GPU reservation verification unavailable") from exc
        actual = result.decode() if isinstance(result, bytes) else result
        if actual != lease.record:
            raise GpuLeaseUnavailable("physical GPU reservation is no longer held")


def remote_dispatch_is_fenced(*, backend_enforces_token: bool) -> bool:
    """Never promote raw llama.cpp based only on an external reservation."""
    return bool(backend_enforces_token)
