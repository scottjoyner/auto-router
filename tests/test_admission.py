import asyncio

import pytest

from auto_router.admission import RuntimeAdmissionController
from auto_router.models import ModelConfig, ProviderCandidate, ProviderConfig
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
