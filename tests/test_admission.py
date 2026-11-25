import asyncio

import pytest

from auto_router.admission import RuntimeAdmissionController
from auto_router.models import ModelConfig, Priority, ProviderCandidate, ProviderConfig
from auto_router.providers import ProviderError


def _candidate(
    *,
    runtime_instance_id: str | None = "lmstudio-xwing-1234",
    slots: int = 1,
    queue_limit: int = 1,
    queue_timeout_seconds: float = 0.25,
) -> ProviderCandidate:
    provider = ProviderConfig(
        name="local-runtime",
        type="lmstudio",
        node_id="xwing",
        runtime_instance_id=runtime_instance_id,
        parallel_slots=slots,
        queue_limit=queue_limit,
        queue_timeout_seconds=queue_timeout_seconds,
        base_url="http://xwing:1234/v1",
        quota_class="local",
        models=[
            ModelConfig(
                alias="local/reconciliation-default",
                provider_model="test-model",
                capabilities={"chat", "streaming"},
            )
        ],
    )
    return ProviderCandidate(provider=provider, model=provider.models[0])


@pytest.mark.asyncio
async def test_unknown_runtime_identity_forces_zero_capacity() -> None:
    candidate = _candidate(runtime_instance_id=None, slots=8)
    controller = RuntimeAdmissionController([candidate.provider])

    with pytest.raises(ProviderError, match="capacity is zero or unknown") as exc:
        await controller.acquire(candidate)

    assert exc.value.status_code == 503
    assert controller.snapshot()[0]["parallel_slots"] == 0


@pytest.mark.asyncio
async def test_one_slot_runtime_serializes_waiting_request() -> None:
    candidate = _candidate()
    controller = RuntimeAdmissionController([candidate.provider])
    first = await controller.acquire(candidate)

    second_task = asyncio.create_task(controller.acquire(candidate))
    await asyncio.sleep(0)
    snapshot = controller.snapshot()[0]
    assert snapshot["active"] == 1
    assert snapshot["queued"] == 1

    await first.release()
    second = await second_task
    snapshot = controller.snapshot()[0]
    assert snapshot["active"] == 1
    assert snapshot["queued"] == 0

    await second.release()
    assert controller.snapshot()[0]["active"] == 0


@pytest.mark.asyncio
async def test_critical_voice_request_advances_ahead_of_background_queue() -> None:
    candidate = _candidate(queue_limit=2, queue_timeout_seconds=1.0)
    controller = RuntimeAdmissionController([candidate.provider])
    first = await controller.acquire(candidate, Priority.batch)

    background_task = asyncio.create_task(
        controller.acquire(candidate, Priority.background)
    )
    await asyncio.sleep(0)
    critical_task = asyncio.create_task(
        controller.acquire(candidate, Priority.critical)
    )
    await asyncio.sleep(0)

    snapshot = controller.snapshot()[0]
    assert snapshot["queued"] == 2
    assert snapshot["queued_by_priority"]["critical"] == 1
    assert snapshot["queued_by_priority"]["background"] == 1

    await first.release()
    critical = await asyncio.wait_for(critical_task, timeout=0.2)
    assert not background_task.done()

    await critical.release()
    background = await asyncio.wait_for(background_task, timeout=0.2)
    await background.release()


@pytest.mark.asyncio
async def test_equal_priority_waiters_remain_fifo() -> None:
    candidate = _candidate(queue_limit=2, queue_timeout_seconds=1.0)
    controller = RuntimeAdmissionController([candidate.provider])
    first = await controller.acquire(candidate)

    earlier_task = asyncio.create_task(
        controller.acquire(candidate, Priority.interactive)
    )
    await asyncio.sleep(0)
    later_task = asyncio.create_task(
        controller.acquire(candidate, Priority.interactive)
    )
    await asyncio.sleep(0)

    await first.release()
    earlier = await asyncio.wait_for(earlier_task, timeout=0.2)
    assert not later_task.done()

    await earlier.release()
    later = await asyncio.wait_for(later_task, timeout=0.2)
    await later.release()


@pytest.mark.asyncio
async def test_bounded_queue_rejects_overflow() -> None:
    candidate = _candidate(queue_limit=1)
    controller = RuntimeAdmissionController([candidate.provider])
    first = await controller.acquire(candidate)
    queued_task = asyncio.create_task(controller.acquire(candidate))
    await asyncio.sleep(0)

    with pytest.raises(ProviderError, match="queue full") as exc:
        await controller.acquire(candidate)

    assert exc.value.status_code == 429
    await first.release()
    queued = await queued_task
    await queued.release()
    assert controller.snapshot()[0]["rejected_total"] == 1


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_leak_queue_or_slot() -> None:
    candidate = _candidate(queue_limit=1)
    controller = RuntimeAdmissionController([candidate.provider])
    first = await controller.acquire(candidate)
    queued_task = asyncio.create_task(controller.acquire(candidate))
    await asyncio.sleep(0)

    queued_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued_task

    snapshot = controller.snapshot()[0]
    assert snapshot["active"] == 1
    assert snapshot["queued"] == 0
    assert snapshot["cancelled_total"] == 1

    await first.release()
    replacement = await controller.acquire(candidate)
    await replacement.release()
    assert controller.snapshot()[0]["active"] == 0


@pytest.mark.asyncio
async def test_lease_context_releases_after_exception() -> None:
    candidate = _candidate()
    controller = RuntimeAdmissionController([candidate.provider])

    with pytest.raises(RuntimeError, match="synthetic failure"):
        async with await controller.acquire(candidate):
            raise RuntimeError("synthetic failure")

    assert controller.snapshot()[0]["active"] == 0


@pytest.mark.asyncio
async def test_cancelled_release_under_lock_does_not_strand_slot() -> None:
    candidate = _candidate(queue_limit=1, queue_timeout_seconds=1.0)
    controller = RuntimeAdmissionController([candidate.provider])
    lease = await controller.acquire(candidate)
    gate = lease._gate
    async with gate._lock:
        releasing = asyncio.create_task(lease.release())
        await asyncio.sleep(0)
        releasing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await releasing
    await lease.release()
    assert controller.snapshot()[0]["active"] == 0
    replacement = await asyncio.wait_for(controller.acquire(candidate), timeout=0.2)
    await replacement.release()


@pytest.mark.asyncio
async def test_cancelled_release_still_hands_off_to_queued_request() -> None:
    candidate = _candidate(queue_limit=1, queue_timeout_seconds=1.0)
    controller = RuntimeAdmissionController([candidate.provider])
    lease = await controller.acquire(candidate)
    waiter = asyncio.create_task(controller.acquire(candidate))
    await asyncio.sleep(0)
    async with lease._gate._lock:
        releasing = asyncio.create_task(lease.release())
        await asyncio.sleep(0)
        releasing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await releasing
    replacement = await asyncio.wait_for(waiter, timeout=0.2)
    await lease.release()
    assert controller.snapshot()[0]["active"] == 1
    await replacement.release()
    assert controller.snapshot()[0]["active"] == 0


@pytest.mark.asyncio
async def test_concurrent_release_is_idempotent() -> None:
    candidate = _candidate(queue_limit=1, queue_timeout_seconds=1.0)
    controller = RuntimeAdmissionController([candidate.provider])
    lease = await controller.acquire(candidate)
    waiter = asyncio.create_task(controller.acquire(candidate))
    await asyncio.sleep(0)
    await asyncio.gather(lease.release(), lease.release())
    replacement = await asyncio.wait_for(waiter, timeout=0.2)
    assert controller.snapshot()[0]["active"] == 1
    await replacement.release()
    assert controller.snapshot()[0]["active"] == 0


@pytest.mark.asyncio
async def test_generation_handover_fences_old_physical_runtime() -> None:
    candidate = _candidate(queue_limit=1, queue_timeout_seconds=1.0)
    old = RuntimeAdmissionController([candidate.provider])
    lease = await old.acquire(candidate)
    queued = asyncio.create_task(old.acquire(candidate))
    await asyncio.sleep(0)
    new = RuntimeAdmissionController([candidate.provider])
    new.inherit_retired_capacity(old)
    with pytest.raises(ProviderError, match="retired generation drain"):
        await new.acquire(candidate)
    with pytest.raises(ProviderError, match="admission retired"):
        await old.acquire(candidate)
    await lease.release()
    prior = await asyncio.wait_for(queued, timeout=0.2)
    with pytest.raises(ProviderError, match="retired generation drain"):
        await new.acquire(candidate)
    await prior.release()
    current = await new.acquire(candidate)
    await current.release()


@pytest.mark.asyncio
async def test_runtime_removed_then_reintroduced_retains_capacity_fence() -> None:
    candidate = _candidate()
    first = RuntimeAdmissionController([candidate.provider])
    lease = await first.acquire(candidate)
    removed = RuntimeAdmissionController([])
    removed.inherit_retired_capacity(first)
    restored = RuntimeAdmissionController([candidate.provider])
    restored.inherit_retired_capacity(removed)
    with pytest.raises(ProviderError, match="retired generation drain"):
        await restored.acquire(candidate)
    await lease.release()
    current = await restored.acquire(candidate)
    await current.release()
