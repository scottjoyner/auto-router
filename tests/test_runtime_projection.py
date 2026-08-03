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


def sign(payload: dict) -> dict:
    payload["checksum"] = projection_checksum(payload)
    payload["signature"] = projection_signature(
        int(payload["generation"]),
        payload["checksum"],
        int(payload["generated_at_ms"]),
        int(payload["expires_at_ms"]),
        SECRET,
    )
    return payload


def document(
    generation: int = 1,
    *,
    item: ProviderConfig | None = None,
    generated_at_ms: int = 1_000_000,
    expires_at_ms: int = 1_060_000,
) -> dict:
    return sign(
        {
            "schema_version": "1",
            "source": "assistx",
            "generation": generation,
            "revision": f"revision-{generation}",
            "generated_at_ms": generated_at_ms,
            "expires_at_ms": expires_at_ms,
            "providers": [(item or provider()).model_dump(mode="json")],
        }
    )


def install_manager_fixtures(monkeypatch) -> None:
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
    monkeypatch.setenv("AUTO_ROUTER_STRICT_OFFLINE", "true")
    monkeypatch.setenv("AUTO_ROUTER_RUNTIME_PROJECTION_HMAC_SECRET", SECRET)


def test_projection_validates_signature_identity_capacity_and_private_paths(
    monkeypatch,
):
    monkeypatch.setenv("AUTO_ROUTER_STRICT_OFFLINE", "true")
    parsed = validate_projection_document(
        document(),
        secret=SECRET,
        now_ms=1_010_000,
    )

    assert parsed.generation == 1
    assert parsed.providers[0].runtime_instance_id == "lmstudio-xwing-1234"
    assert parsed.providers[0].models[0].quantization == "Q4_K_M"


def test_projection_rejects_tamper_public_path_unknown_identity_and_expiry(
    monkeypatch,
):
    monkeypatch.setenv("AUTO_ROUTER_STRICT_OFFLINE", "true")

    tampered = document()
    tampered["providers"][0]["parallel_slots"] = 99
    with pytest.raises(ValueError, match="checksum mismatch"):
        validate_projection_document(tampered, secret=SECRET, now_ms=1_010_000)

    expiry_tamper = document()
    expiry_tamper["expires_at_ms"] = 1_070_000
    with pytest.raises(ValueError, match="signature mismatch"):
        validate_projection_document(
            expiry_tamper,
            secret=SECRET,
            now_ms=1_010_000,
        )

    public = document(item=provider(base_url="https://api.example.com/v1"))
    with pytest.raises(ValueError, match="not allowed"):
        validate_projection_document(public, secret=SECRET, now_ms=1_010_000)

    unresolved = provider()
    unresolved.runtime_version = "unknown"
    invalid = document(item=unresolved)
    with pytest.raises(ValueError, match="runtime_version must be resolved"):
        validate_projection_document(invalid, secret=SECRET, now_ms=1_010_000)

    expired = document(generated_at_ms=900_000, expires_at_ms=999_999)
    with pytest.raises(ValueError, match="is expired"):
        validate_projection_document(expired, secret=SECRET, now_ms=1_000_000)


@pytest.mark.asyncio
async def test_atomic_generation_swap_preserves_active_old_lease(monkeypatch):
    install_manager_fixtures(monkeypatch)
    monkeypatch.setattr(
        "auto_router.runtime_projection.time.time",
        lambda: 1010.0,
    )
    state = SimpleNamespace(agents=SimpleNamespace(), policies=SimpleNamespace())
    manager = RuntimeProjectionManager(state)

    result = await manager.apply(document(1))
    assert result["applied"] is True

    first_provider = state.providers.enabled()[0]
    lease = await state.admission.acquire(
        ProviderCandidate(
            provider=first_provider,
            model=first_provider.models[0],
        )
    )

    result = await manager.apply(
        document(
            2,
            item=provider(slots=2),
            generated_at_ms=1_010_000,
            expires_at_ms=1_070_000,
        )
    )

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
async def test_same_generation_refreshes_signed_lease_without_rebuilding_gates(
    monkeypatch,
):
    install_manager_fixtures(monkeypatch)
    monkeypatch.setattr(
        "auto_router.runtime_projection.time.time",
        lambda: 1010.0,
    )
    state = SimpleNamespace(agents=SimpleNamespace(), policies=SimpleNamespace())
    manager = RuntimeProjectionManager(state)

    first = document(1)
    await manager.apply(first)
    original_admission = state.admission
    original_access_paths = state.access_paths
    original_checksum = manager.current.checksum if manager.current else None

    refreshed = document(
        1,
        generated_at_ms=1_020_000,
        expires_at_ms=1_080_000,
    )
    assert refreshed["checksum"] == first["checksum"]
    result = await manager.apply(refreshed)

    assert result["idempotent"] is True
    assert result["lease_refreshed"] is True
    assert state.admission is original_admission
    assert state.access_paths is original_access_paths
    assert manager.current is not None
    assert manager.current.checksum == original_checksum
    assert manager.current.generated_at_ms == 1_020_000
    assert manager.current.expires_at_ms == 1_080_000


@pytest.mark.asyncio
async def test_generation_conflict_skip_and_expiry_fail_closed(monkeypatch):
    install_manager_fixtures(monkeypatch)
    monkeypatch.setattr(
        "auto_router.runtime_projection.time.time",
        lambda: 1010.0,
    )
    state = SimpleNamespace(agents=SimpleNamespace(), policies=SimpleNamespace())
    manager = RuntimeProjectionManager(state)
    await manager.apply(document(1))

    conflict = document(1, item=provider(slots=2))
    with pytest.raises(ValueError, match="checksum conflict"):
        await manager.apply(conflict)

    with pytest.raises(ValueError, match="advance exactly by one"):
        await manager.apply(
            document(
                3,
                generated_at_ms=1_010_000,
                expires_at_ms=1_070_000,
            )
        )

    manager.assert_current_fresh(now_ms=1_059_999)
    with pytest.raises(RuntimeError, match="is expired"):
        manager.assert_current_fresh(now_ms=1_060_000)
    assert manager.last_error.endswith("is expired")
