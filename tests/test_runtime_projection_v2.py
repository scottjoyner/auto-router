from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_router import runtime_projection as legacy
from auto_router.models import ModelConfig, ProviderConfig
from auto_router.runtime_projection_v2 import (
    RuntimeProjectionManager,
    signing_message,
    validate_projection_document,
)


KEY_ID = "projection-key-2026"


def provider(*, slots: int = 1) -> ProviderConfig:
    return ProviderConfig(
        name="assistx-xwing",
        type="lmstudio",
        node_id="xwing",
        runtime_instance_id="lmstudio-xwing-1234",
        runtime_kind="lmstudio",
        runtime_version="0.4.7",
        headless=False,
        parallel_slots=slots,
        queue_limit=4,
        queue_timeout_seconds=30,
        enabled=True,
        base_url="http://192.168.1.9:1234/v1",
        access_urls=[
            "http://192.168.1.9:1234/v1",
            "http://100.64.0.9:1234/v1",
        ],
        quota_class="local",
        models=[
            ModelConfig(
                alias="local/qwen",
                provider_model="qwen.gguf",
                model_instance_id="model-xwing-1",
                artifact_fingerprint="sha256:abcdef",
                quantization="Q4_K_M",
                context_window=32768,
                capabilities={"chat", "streaming", "local_only"},
            )
        ],
    )


def sign_document(
    private_key: Ed25519PrivateKey,
    generation: int = 1,
    *,
    item: ProviderConfig | None = None,
    generated_at_ms: int = 1_000_000,
    expires_at_ms: int = 1_060_000,
) -> dict:
    payload = {
        "schema_version": "2",
        "source": "assistx",
        "generation": generation,
        "revision": f"revision-{generation}",
        "generated_at_ms": generated_at_ms,
        "expires_at_ms": expires_at_ms,
        "providers": [(item or provider()).model_dump(mode="json")],
        "signature_algorithm": "Ed25519",
        "signature_key_id": KEY_ID,
    }
    payload["checksum"] = legacy.projection_checksum(payload)
    payload["signature"] = base64.urlsafe_b64encode(
        private_key.sign(signing_message(payload))
    ).decode("ascii").rstrip("=")
    return payload


def configure_key(monkeypatch, private_key: Ed25519PrivateKey) -> None:
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    monkeypatch.setenv("AUTO_ROUTER_STRICT_OFFLINE", "true")
    monkeypatch.setenv("AUTO_ROUTER_RUNTIME_PROJECTION_KEY_ID", KEY_ID)
    monkeypatch.setenv("AUTO_ROUTER_RUNTIME_PROJECTION_VERIFY_KEY_PEM", public_pem)


def install_manager_fixtures(monkeypatch) -> None:
    async def fake_context(*_args, **_kwargs):
        return SimpleNamespace(revision="context")

    class FakePolicyEngine:
        def __init__(self, providers, policies, profile, context):
            self.providers = providers
            self.policies = policies
            self.profile = profile
            self.context = context

    monkeypatch.setattr(legacy, "load_context_snapshot_async", fake_context)
    monkeypatch.setattr(legacy, "PolicyEngine", FakePolicyEngine)
    monkeypatch.setattr(
        legacy,
        "get_settings",
        lambda: SimpleNamespace(context_config="", default_profile="local_only"),
    )


def test_ed25519_projection_validates_and_rejects_tamper(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    configure_key(monkeypatch, private_key)
    payload = sign_document(private_key)

    document, converted = validate_projection_document(payload, now_ms=1_010_000)
    assert document.schema_version == "2"
    assert document.signature_key_id == KEY_ID
    assert converted["schema_version"] == "1"

    tampered = sign_document(private_key)
    tampered["providers"][0]["parallel_slots"] = 9
    with pytest.raises(ValueError, match="checksum mismatch"):
        validate_projection_document(tampered, now_ms=1_010_000)

    expiry_tamper = sign_document(private_key)
    expiry_tamper["expires_at_ms"] = 1_070_000
    with pytest.raises(ValueError, match="signature mismatch"):
        validate_projection_document(expiry_tamper, now_ms=1_010_000)


def test_projection_rejects_wrong_key_and_key_id(monkeypatch):
    signer = Ed25519PrivateKey.generate()
    verifier = Ed25519PrivateKey.generate()
    configure_key(monkeypatch, verifier)

    with pytest.raises(ValueError, match="signature mismatch"):
        validate_projection_document(sign_document(signer), now_ms=1_010_000)

    configure_key(monkeypatch, signer)
    payload = sign_document(signer)
    payload["signature_key_id"] = "retired-key"
    payload["checksum"] = legacy.projection_checksum(payload)
    payload["signature"] = base64.urlsafe_b64encode(
        signer.sign(signing_message(payload))
    ).decode("ascii").rstrip("=")
    with pytest.raises(ValueError, match="key id is not accepted"):
        validate_projection_document(payload, now_ms=1_010_000)


@pytest.mark.asyncio
async def test_manager_applies_and_refreshes_same_ed25519_generation(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    configure_key(monkeypatch, private_key)
    install_manager_fixtures(monkeypatch)
    monkeypatch.setattr(legacy.time, "time", lambda: 1010.0)

    state = SimpleNamespace(agents=SimpleNamespace(), policies=SimpleNamespace())
    manager = RuntimeProjectionManager(state)
    first = sign_document(private_key)
    result = await manager.apply(first)
    assert result["applied"] is True
    assert manager.current is not None
    assert manager.current.checksum == first["checksum"]

    refreshed = sign_document(
        private_key,
        generated_at_ms=1_020_000,
        expires_at_ms=1_080_000,
    )
    assert refreshed["checksum"] == first["checksum"]
    result = await manager.apply(refreshed)
    assert result["idempotent"] is True
    assert result["lease_refreshed"] is True
    assert manager.current.expires_at_ms == 1_080_000

    conflict = sign_document(private_key, item=provider(slots=2))
    with pytest.raises(ValueError, match="checksum conflict"):
        await manager.apply(conflict)
