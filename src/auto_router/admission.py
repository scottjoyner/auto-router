from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from auto_router.models import ProviderCandidate, ProviderConfig
from auto_router.providers import ProviderError


@dataclass(frozen=True)
class RuntimeAdmissionConfig:
    runtime_instance_id: str
    parallel_slots: int
    queue_limit: int
    queue_timeout_seconds: float


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
        self._condition = asyncio.Condition()
        self.active = 0
        self.queued = 0
        self.acquired_total = 0
        self.rejected_total = 0
        self.timed_out_total = 0
        self.cancelled_total = 0

    async def acquire(self) -> RuntimeAdmissionLease:
        runtime_id = self.config.runtime_instance_id
        if self.config.parallel_slots <= 0:
            self.rejected_total += 1
            raise ProviderError(
                f"runtime admission unavailable for {runtime_id}: capacity is zero or unknown",
                status_code=503,
                retryable=True,
            )

        async with self._condition:
            if self.active < self.config.parallel_slots:
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

            self.queued += 1
            try:
                await asyncio.wait_for(
                    self._wait_for_capacity(),
                    timeout=self.config.queue_timeout_seconds,
                )
            except TimeoutError as exc:
                self.queued -= 1
                self.timed_out_total += 1
                raise ProviderError(
                    f"runtime admission timed out for {runtime_id}",
                    status_code=503,
                    retryable=True,
                ) from exc
            except asyncio.CancelledError:
                self.queued -= 1
                self.cancelled_total += 1
                self._condition.notify(1)
                raise

            self.queued -= 1
            self.active += 1
            self.acquired_total += 1
            return RuntimeAdmissionLease(self)

    async def _wait_for_capacity(self) -> None:
        while self.active >= self.config.parallel_slots:
            await self._condition.wait()

    async def release(self) -> None:
        async with self._condition:
            if self.active <= 0:
                return
            self.active -= 1
            self._condition.notify(1)

    def snapshot(self) -> dict[str, Any]:
        return {
            "runtime_instance_id": self.config.runtime_instance_id,
            "parallel_slots": self.config.parallel_slots,
            "queue_limit": self.config.queue_limit,
            "queue_timeout_seconds": self.config.queue_timeout_seconds,
            "active": self.active,
            "queued": self.queued,
            "acquired_total": self.acquired_total,
            "rejected_total": self.rejected_total,
            "timed_out_total": self.timed_out_total,
            "cancelled_total": self.cancelled_total,
        }


class RuntimeAdmissionController:
    """Per-physical-runtime admission control for the strict-offline entrypoint."""

    def __init__(self, providers: Iterable[ProviderConfig]) -> None:
        self._gates: dict[str, _RuntimeGate] = {}
        self._provider_runtime_keys: dict[str, str] = {}

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

    def runtime_key(self, candidate: ProviderCandidate) -> str:
        return self._provider_runtime_keys.get(
            candidate.provider.name,
            f"unresolved:{candidate.provider.name}",
        )

    async def acquire(self, candidate: ProviderCandidate) -> RuntimeAdmissionLease:
        runtime_key = self.runtime_key(candidate)
        gate = self._gates.get(runtime_key)
        if gate is None:
            raise ProviderError(
                f"runtime admission unavailable for {runtime_key}: no admission record",
                status_code=503,
                retryable=True,
            )
        return await gate.acquire()

    def snapshot(self) -> list[dict[str, Any]]:
        return [self._gates[key].snapshot() for key in sorted(self._gates)]
