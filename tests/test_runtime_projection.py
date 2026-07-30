from __future__ import annotations

from types import SimpleNamespace

import pytest

from auto_router.models import ModelConfig, ProviderCandidate, ProviderConfig
from auto_router.runtime_projection import (
    RuntimeProjectionManager,
    projection_checksum,
    projection_signature,
    validate_projection_document,
)


SECRET = "projection-test-secret"


def provider(
    *,
    slots: int = 1,
    runtime_id: str = "lmstudio-xwing-1234",
    base_url: str = "http://192.168.1.9:1234/v1",
) -> ProviderConfig:
    return ProviderConfig(
        name="assistx-xwing",
        type="lmstudio",
        node_id="xwing",
        runtime_instance_id=runtime_id,
        runtime_kind="lmstudio",
        runtime_version="0.4.7",
        headless=False,
        parallel_slots=slots,
        queue_limit=4,
        queue_timeout_seconds=30,
        enabled=True,
        base_url=base_url,
        access_urls=[base_url, "http://100.64.0.9:1234/v1"],
        quota_class="local",
        models=[
            ModelConfig(
                alias="local/qwen",
                provider_model="qwen.gguf",
                model_instance_id="model-xwing-1",
                artifact_fingerprint="sha256:abcdef",
                quantization="Q4_K_M",
                context_window=32768,
                capabilities={"chat", "streaming", "code", "local_only"},
            )
        ],
    )


def document(generation: int = 1, *, item: ProviderConfig | None = None) -> dict:
    payload = {
        "schema_version": "1",
        "source": "assistx",
        "generation": generation,
        "revision": f"revision-{generation}",
        "generated_at_ms": 1_000_000,
        "expires_at_ms": 1_060_000,
        "providers": [(item or provider()).model_dump(mode="json")],
    }
    payload["checksum"] = projection_checksum(payload)
    payload["signature"] = projection_signature(
        generation,
        payload["checksum"],
        SECRET,
    )
    return payload


def test_projection_validates_signature_identity_capacity_and_private_paths(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_STRICT_OFFLINE", "true")
    parsed = validate_projection_document(
        document(),
        secret=SECRET,
        now_ms=1_010_000,
    )

    assert parsed.generation == 1
    assert parsed.providers[0].runtime_instance_id == "lmstudio-xwing-1234"
    assert parsed.providers[0].models[0].quantization == "Q4_K_M"


def test_projection_rejects_tamper_public_path_and_unknown_identity(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_STRICT_OFFLINE", "true")
    tampered = document()
    tampered["providers"][0]["parallel_slots"] = 99
    with pytest.raises(ValueError, match="checksum mismatch"):
        validate_projection_document(tampered, secret=SECRET, now_ms=1_010_000)

    public = document(item=provider(base_url="https://api.example.com/v1"))
    with pytest.raises(ValueError, match="not allowed"):
        validate_projection_document(public, secret=SECRET, now_ms=1_010_000)

    unresolved = provider()
    unresolved.runtime_version = "unknown"
    invalid = document(item=unresolved)
    with pytest.raises(ValueError, match="runtime_version must be resolved"):
        validate_projection_document(invalid, secret=SECRET, now_ms=1_010_000)


@pytest.mark.asyncio
async def test_atomic_generation_swap_preserves_active_old_lease(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_STRICT_OFFLINE", "true")
    monkeypatch.setenv("AUTO_ROUTER_RUNTIME_PROJECTION_HMAC_SECRET", SECRET)

    async def fake_context(*_args, **_kwargs):
        return SimpleNamespace(revision="context")

    class FakePolicyEngine:
        def __init__(self, providers, policies, profile, context):
            self.providers = providers
            self.policies = policies
            self.profile = profile
            self.context = context

    monkeypatch.setattr(
        "auto_router.runtime_projection.load_context_snapshot_async",
        fake_context,
    )
    monkeypatch.setattr(
        "auto_router.runtime_projection.PolicyEngine",
        FakePolicyEngine,
    )
    monkeypatch.setattr(
        "auto_router.runtime_projection.get_settings",
        lambda: SimpleNamespace(
            context_config="",
            default_profile="local_only",
        ),
    )

    state = SimpleNamespace(agents=SimpleNamespace(), policies=SimpleNamespace())
    manager = RuntimeProjectionManager(state)

    first = document(1)
    first["generated_at_ms"] = int(1_000_000)
    first["expires_at_ms"] = int(1_060_000)
    first["checksum"] = projection_checksum(first)
    first["signature"] = projection_signature(1, first["checksum"], SECRET)
    monkeypatch.setattr(
        "auto_router.runtime_projection.time.time",
        lambda: 1010.0,
    )
    result = await manager.apply(first)
    assert result["applied"] is True

    first_provider = state.providers.enabled()[0]
    lease = await state.admission.acquire(
        ProviderCandidate(
            provider=first_provider,
            model=first_provider.models[0],
        )
    )

    second_provider = provider(slots=2)
    second = document(2, item=second_provider)
    second["generated_at_ms"] = 1_010_000
    second["expires_at_ms"] = 1_070_000
    second["checksum"] = projection_checksum(second)
    second["signature"] = projection_signature(2, second["checksum"], SECRET)
    result = await manager.apply(second)

    assert result["applied"] is True
    assert manager.current is not None
    assert manager.current.generation == 2
    assert state.admission.snapshot()[0]["parallel_slots"] == 2
    assert len(manager.retired) == 1
    assert manager.retired[0].admission.snapshot()[0]["active"] == 1

    await lease.release()
    status = manager.status()
    assert status["retired_generations"] == []


@pytest.mark.asyncio
async def test_generation_replay_is_idempotent_and_conflict_is_rejected(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_STRICT_OFFLINE", "true")
    monkeypatch.setenv("AUTO_ROUTER_RUNTIME_PROJECTION_HMAC_SECRET", SECRET)
    monkeypatch.setattr(
        "auto_router.runtime_projection.load_context_snapshot_async",
        lambda *_args, **_kwargs: None,
    )

    async def fake_context(*_args, **_kwargs):
        return SimpleNamespace(revision="context")

    monkeypatch.setattr(
        "auto_router.runtime_projection.load_context_snapshot_async",
        fake_context,
    )
    monkeypatch.setattr(
        "auto_router.runtime_projection.PolicyEngine",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "auto_router.runtime_projection.get_settings",
        lambda: SimpleNamespace(context_config="", default_profile="local_only"),
    )
    monkeypatch.setattr(
        "auto_router.runtime_projection.time.time",
        lambda: 1010.0,
    )
    state = SimpleNamespace(agents=SimpleNamespace(), policies=SimpleNamespace())
    manager = RuntimeProjectionManager(state)
    payload = document(1)

    await manager.apply(payload)
    replay = await manager.apply(payload)
    assert replay["idempotent"] is True

    conflict = document(1, item=provider(slots=2))
    with pytest.raises(ValueError, match="checksum conflict"):
        await manager.apply(conflict)
