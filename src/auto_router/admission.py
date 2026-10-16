from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from auto_router.models import Priority, ProviderCandidate, ProviderConfig
from auto_router.providers import ProviderError


_PRIORITY_RANK: dict[Priority, int] = {
    Priority.critical: 0,
    Priority.repo_critical: 1,
    Priority.interactive: 2,
    Priority.local_only: 3,
    Priority.batch: 4,
    Priority.background: 5,
}


@dataclass(frozen=True)
class RuntimeAdmissionConfig:
    runtime_instance_id: str
    parallel_slots: int
    queue_limit: int
    queue_timeout_seconds: float


@dataclass
class _QueuedWaiter:
    priority: Priority
    sequence: int
    future: asyncio.Future[RuntimeAdmissionLease]

    @property
    def sort_key(self) -> tuple[int, int]:
        return (_PRIORITY_RANK[self.priority], self.sequence)


class RuntimeAdmissionLease:
    def __init__(self, gate: _RuntimeGate) -> None:
        self._gate = gate
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._gate.release()

    async def __aenter__(self) -> RuntimeAdmissionLease:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.release()


class _RuntimeGate:
    def __init__(self, config: RuntimeAdmissionConfig) -> None:
        self.config = config
        self._lock = asyncio.Lock()
        self._waiters: list[_QueuedWaiter] = []
        self._sequence = 0
        self.active = 0
        self.acquired_total = 0
        self.rejected_total = 0
        self.timed_out_total = 0
        self.cancelled_total = 0

    @property
    def queued(self) -> int:
        return len(self._waiters)

    async def acquire(
        self,
        priority: Priority = Priority.interactive,
    ) -> RuntimeAdmissionLease:
        runtime_id = self.config.runtime_instance_id
        if self.config.parallel_slots <= 0:
            self.rejected_total += 1
            raise ProviderError(
                f"runtime admission unavailable for {runtime_id}: capacity is zero or unknown",
                status_code=503,
                retryable=True,
            )

        async with self._lock:
            if self.active < self.config.parallel_slots and not self._waiters:
                self.active += 1
                self.acquired_total += 1
                return RuntimeAdmissionLease(self)

            if self.config.queue_limit <= 0 or self.queued >= self.config.queue_limit:
                self.rejected_total += 1
                raise ProviderError(
                    f"runtime admission queue full for {runtime_id}",
                    status_code=429,
                    retryable=True,
                )

            loop = asyncio.get_running_loop()
            waiter = _QueuedWaiter(
                priority=priority,
                sequence=self._sequence,
                future=loop.create_future(),
            )
            self._sequence += 1
            self._waiters.append(waiter)

        try:
            return await asyncio.wait_for(
                asyncio.shield(waiter.future),
                timeout=self.config.queue_timeout_seconds,
            )
        except TimeoutError as exc:
            async with self._lock:
                if waiter.future.done():
                    return waiter.future.result()
                self._remove_waiter(waiter)
                waiter.future.cancel()
                self.timed_out_total += 1
            raise ProviderError(
                f"runtime admission timed out for {runtime_id}",
                status_code=503,
                retryable=True,
            ) from exc
        except asyncio.CancelledError:
            granted_lease: RuntimeAdmissionLease | None = None
            async with self._lock:
                if waiter.future.done() and not waiter.future.cancelled():
                    granted_lease = waiter.future.result()
                else:
                    self._remove_waiter(waiter)
                    waiter.future.cancel()
                self.cancelled_total += 1
            if granted_lease is not None:
                await granted_lease.release()
            raise

    def _remove_waiter(self, waiter: _QueuedWaiter) -> None:
        try:
            self._waiters.remove(waiter)
        except ValueError:
            pass

    def _next_waiter(self) -> _QueuedWaiter | None:
        live = [waiter for waiter in self._waiters if not waiter.future.done()]
        if not live:
            self._waiters.clear()
            return None
        waiter = min(live, key=lambda item: item.sort_key)
        self._remove_waiter(waiter)
        return waiter

    async def release(self) -> None:
        async with self._lock:
            if self.active <= 0:
                return
            self.active -= 1
            waiter = self._next_waiter()
            if waiter is None:
                return

            # Transfer the newly free slot directly to the highest-priority
            # waiter. This avoids a race where a later request could bypass the
            # existing queue between release and wake-up.
            self.active += 1
            self.acquired_total += 1
            waiter.future.set_result(RuntimeAdmissionLease(self))

    def snapshot(self) -> dict[str, Any]:
        queued_by_priority = {
            priority.value: sum(1 for waiter in self._waiters if waiter.priority == priority)
            for priority in Priority
        }
        return {
            "runtime_instance_id": self.config.runtime_instance_id,
            "parallel_slots": self.config.parallel_slots,
            "queue_limit": self.config.queue_limit,
            "queue_timeout_seconds": self.config.queue_timeout_seconds,
            "active": self.active,
            "queued": self.queued,
            "queued_by_priority": queued_by_priority,
            "acquired_total": self.acquired_total,
            "rejected_total": self.rejected_total,
            "timed_out_total": self.timed_out_total,
            "cancelled_total": self.cancelled_total,
        }


class PhysicalGpuAdmissionPool:
    """Physical-card capacity survives signed projection generation swaps."""

    def __init__(self) -> None:
        self.gates: dict[str, _RuntimeGate] = {}

    def gate(self, group_id: str, slots: int) -> _RuntimeGate:
        config = RuntimeAdmissionConfig(
            runtime_instance_id=f"physical-gpu:{group_id}",
            parallel_slots=slots,
            queue_limit=0,
            queue_timeout_seconds=0,
        )
        existing = self.gates.get(group_id)
        if existing is not None and existing.config != config:
            raise RuntimeError(f"conflicting physical GPU capacity for {group_id}")
        return self.gates.setdefault(group_id, _RuntimeGate(config))


class _CompositeAdmissionLease:
    """Return both per-runtime and physical-card capacity on every exit."""

    def __init__(
        self, runtime: RuntimeAdmissionLease, gpu: RuntimeAdmissionLease,
    ) -> None:
        self.runtime = runtime
        self.gpu = gpu
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            await self.gpu.release()
        finally:
            await self.runtime.release()

    async def __aenter__(self) -> _CompositeAdmissionLease:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.release()


class RuntimeAdmissionController:
    """Per-runtime admission plus optional shared physical GPU gates."""

    def __init__(
        self,
        providers: Iterable[ProviderConfig],
        *,
        gpu_pool: PhysicalGpuAdmissionPool | None = None,
    ) -> None:
        self._gates: dict[str, _RuntimeGate] = {}
        self._provider_runtime_keys: dict[str, str] = {}
        self._provider_gpu_keys: dict[str, str] = {}
        self.gpu_pool = gpu_pool if gpu_pool is not None else PhysicalGpuAdmissionPool()

        for provider in providers:
            runtime_id = str(provider.runtime_instance_id or "").strip()
            slots = int(provider.parallel_slots)
            if not runtime_id:
                runtime_id = f"unresolved:{provider.name}"
                slots = 0

            config = RuntimeAdmissionConfig(
                runtime_instance_id=runtime_id,
                parallel_slots=slots,
                queue_limit=int(provider.queue_limit),
                queue_timeout_seconds=float(provider.queue_timeout_seconds),
            )
            existing = self._gates.get(runtime_id)
            if existing is not None and existing.config != config:
                raise RuntimeError(
                    f"conflicting admission configuration for runtime {runtime_id}"
                )
            self._gates.setdefault(runtime_id, _RuntimeGate(config))
            self._provider_runtime_keys[provider.name] = runtime_id
            physical_group = str(provider.gpu_resource_group_id or "").strip()
            if physical_group:
                self.gpu_pool.gate(physical_group, int(provider.gpu_parallel_slots))
                self._provider_gpu_keys[provider.name] = physical_group

    def runtime_key(self, candidate: ProviderCandidate) -> str:
        return self._provider_runtime_keys.get(
            candidate.provider.name,
            f"unresolved:{candidate.provider.name}",
        )

    async def acquire(
        self,
        candidate: ProviderCandidate,
        priority: Priority = Priority.interactive,
    ) -> RuntimeAdmissionLease | _CompositeAdmissionLease:
        runtime_key = self.runtime_key(candidate)
        gate = self._gates.get(runtime_key)
        if gate is None:
            raise ProviderError(
                f"runtime admission unavailable for {runtime_key}: no admission record",
                status_code=503,
                retryable=True,
            )
        runtime_lease = await gate.acquire(priority)
        physical_group = self._provider_gpu_keys.get(candidate.provider.name)
        if physical_group is None:
            return runtime_lease
        try:
            # GPU group gates are zero-queue: a busy card rejects immediately,
            # and the runtime slot is always released on failure/cancellation.
            gpu_lease = await self.gpu_pool.gates[physical_group].acquire(priority)
        except BaseException:
            await runtime_lease.release()
            raise
        return _CompositeAdmissionLease(runtime_lease, gpu_lease)

    def snapshot(self) -> list[dict[str, Any]]:
        runtimes = [self._gates[key].snapshot() for key in sorted(self._gates)]
        groups = sorted(set(self._provider_gpu_keys.values()))
        gpu = [
            {**self.gpu_pool.gates[key].snapshot(), "resource_kind": "physical_gpu"}
            for key in groups
        ]
        return runtimes + gpu
