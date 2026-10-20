"""One physical GPU budget across models and signed projection generations."""
from pathlib import Path

import pytest
import yaml

from auto_router.admission import PhysicalGpuAdmissionPool, RuntimeAdmissionController
from auto_router.models import ModelConfig, ProviderCandidate, ProviderConfig
from auto_router.providers import ProviderError


def _candidates():
    path = Path(__file__).resolve().parents[1] / "config/providers.gpu-canary.CANDIDATE.yaml"
    rows = yaml.safe_load(path.read_text())["providers"]
    result = {}
    for row in rows:
        conf = ProviderConfig.model_validate(row)
        assert conf.enabled is False
        assert conf.runtime_instance_id
        assert conf.gpu_resource_group_id
        assert conf.gpu_parallel_slots == 1
        model = conf.models[0] if conf.models else ModelConfig(
            alias="test-only/" + conf.name,
            provider_model="test-only-model",
        )
        result[conf.name] = ProviderCandidate(provider=conf, model=model)
    return result


def _usage(controller):
    return {
        row["runtime_instance_id"]: row
        for row in controller.snapshot()
    }


@pytest.mark.asyncio
async def test_joyner_models_share_one_physical_card_slot_without_conflating_identity():
    c = _candidates()
    mini = c["llama-joyner-r9700-canary"]
    toolcall = c["llama-joyner-r9700-toolcall"]
    assert mini.provider.runtime_instance_id != toolcall.provider.runtime_instance_id
    assert mini.provider.gpu_resource_group_id == toolcall.provider.gpu_resource_group_id
    controller = RuntimeAdmissionController([mini.provider, toolcall.provider])
    lease = await controller.acquire(toolcall)
    before = _usage(controller)
    assert before[toolcall.provider.runtime_instance_id]["active"] == 1
    assert before["physical-gpu:joyner:1002-7551:gfx1201"]["active"] == 1
    with pytest.raises(ProviderError, match="queue full") as exc:
        await controller.acquire(mini)
    assert exc.value.status_code == 429
    assert _usage(controller)[mini.provider.runtime_instance_id]["active"] == 0
    await lease.release()
    await lease.release()
    async with await controller.acquire(mini):
        assert _usage(controller)["physical-gpu:joyner:1002-7551:gfx1201"]["active"] == 1
    assert _usage(controller)["physical-gpu:joyner:1002-7551:gfx1201"]["active"] == 0


@pytest.mark.asyncio
async def test_xwing_rocm_and_vulkan_share_capacity_but_joyner_is_independent():
    c = _candidates()
    rocm = c["llama-xwing-rocm-canary"]
    vulkan = c["llama-xwing-bonsai2-vulkan-canary"]
    joyner = c["llama-joyner-r9700-toolcall"]
    assert rocm.provider.gpu_resource_group_id == vulkan.provider.gpu_resource_group_id
    controller = RuntimeAdmissionController(
        [rocm.provider, vulkan.provider, joyner.provider]
    )
    async with await controller.acquire(rocm):
        with pytest.raises(ProviderError) as exc:
            await controller.acquire(vulkan)
        assert exc.value.status_code == 429
        async with await controller.acquire(joyner):
            x = _usage(controller)
            assert x["physical-gpu:xwing:1002-1586:gfx1151"]["active"] == 1
            assert x["physical-gpu:joyner:1002-7551:gfx1201"]["active"] == 1


@pytest.mark.asyncio
async def test_new_generation_reuses_the_old_physical_gpu_gate_and_release():
    c = _candidates()
    old = c["llama-joyner-r9700-canary"]
    new = c["llama-joyner-r9700-toolcall"]
    pool = PhysicalGpuAdmissionPool()
    first = RuntimeAdmissionController([old.provider], gpu_pool=pool)
    prior_generation_lease = await first.acquire(old)
    next_generation = RuntimeAdmissionController([new.provider], gpu_pool=pool)
    assert next_generation.gpu_pool is first.gpu_pool
    with pytest.raises(ProviderError) as exc:
        await next_generation.acquire(new)
    assert exc.value.status_code == 429
    assert _usage(next_generation)[new.provider.runtime_instance_id]["active"] == 0
    await prior_generation_lease.release()
    async with await next_generation.acquire(new):
        assert _usage(next_generation)["physical-gpu:joyner:1002-7551:gfx1201"]["active"] == 1


@pytest.mark.asyncio
async def test_unconfigured_gpu_group_capacity_denies_and_releases_runtime():
    c = _candidates()["llama-joyner-r9700-toolcall"]
    conf = c.provider.model_copy(update={"gpu_parallel_slots": 0})
    c = c.model_copy(update={"provider": conf})
    controller = RuntimeAdmissionController([conf])
    with pytest.raises(ProviderError, match="capacity is zero or unknown") as exc:
        await controller.acquire(c)
    assert exc.value.status_code == 503
    assert _usage(controller)[conf.runtime_instance_id]["active"] == 0


def test_conflicting_gpu_capacity_cannot_silently_expand_after_generation_swap():
    c = _candidates()["llama-joyner-r9700-toolcall"]
    pool = PhysicalGpuAdmissionPool()
    RuntimeAdmissionController([c.provider], gpu_pool=pool)
    raised = c.provider.model_copy(update={"gpu_parallel_slots": 2})
    with pytest.raises(RuntimeError, match="conflicting physical GPU capacity"):
        RuntimeAdmissionController([raised], gpu_pool=pool)


@pytest.mark.asyncio
async def test_unrelated_legacy_runtime_admission_remains_unchanged():
    legacy = ProviderConfig(
        name="legacy-local", type="lmstudio", base_url="http://127.0.0.1:1234/v1",
        runtime_instance_id="legacy-local-1234", parallel_slots=2, queue_limit=0,
    )
    candidate = ProviderCandidate(
        provider=legacy,
        model=ModelConfig(alias="test-legacy", provider_model="some-model"),
    )
    controller = RuntimeAdmissionController([legacy])
    first = await controller.acquire(candidate)
    second = await controller.acquire(candidate)
    assert len(controller.snapshot()) == 1
    await second.release()
    await first.release()

@pytest.mark.parametrize("group", [None, "", "   "])
def test_nonzero_gpu_slots_without_physical_identity_fail_closed(group):
    c = _candidates()["llama-joyner-r9700-toolcall"]
    unsafe = c.provider.model_copy(update={
        "gpu_resource_group_id": group,
        "gpu_parallel_slots": 1,
    })
    with pytest.raises(RuntimeError, match="lacks physical resource identity"):
        RuntimeAdmissionController([unsafe])


def test_whitespace_only_physical_identity_denied_even_with_zero_gpu_slots():
    c = _candidates()["llama-joyner-r9700-toolcall"]
    unsafe = c.provider.model_copy(update={
        "gpu_resource_group_id": " ",
        "gpu_parallel_slots": 0,
    })
    with pytest.raises(RuntimeError, match="lacks physical resource identity"):
        RuntimeAdmissionController([unsafe])
