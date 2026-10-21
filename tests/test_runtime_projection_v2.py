from __future__ import annotations

import ast
import base64
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_router import runtime_projection as legacy
from auto_router.models import ModelConfig, ProviderCandidate, ProviderConfig
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


def test_reconciled_entrypoint_imports_v2_projection_manager():
    entrypoint = Path(__file__).parents[1] / "src" / "auto_router" / "main_live.py"
    tree = ast.parse(entrypoint.read_text(encoding="utf-8"))
    projection_imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(
            alias.name in {"RuntimeProjectionManager", "projection_poll_task"}
            for alias in node.names
        )
    }

    assert "auto_router.runtime_projection_v2" in projection_imports
    assert "auto_router.runtime_projection" not in projection_imports


@pytest.mark.asyncio
async def test_ed25519_generation_swap_preserves_active_old_lease(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    configure_key(monkeypatch, private_key)
    install_manager_fixtures(monkeypatch)
    monkeypatch.setattr(legacy.time, "time", lambda: 1010.0)

    state = SimpleNamespace(agents=SimpleNamespace(), policies=SimpleNamespace())
    manager = RuntimeProjectionManager(state)

    first = sign_document(private_key, generation=1)
    result = await manager.apply(first)
    assert result["applied"] is True

    first_provider = state.providers.enabled()[0]
    old_admission = state.admission
    lease = await old_admission.acquire(
        ProviderCandidate(
            provider=first_provider,
            model=first_provider.models[0],
        )
    )

    second = sign_document(
        private_key,
        generation=2,
        item=provider(slots=2),
        generated_at_ms=1_010_000,
        expires_at_ms=1_070_000,
    )
    result = await manager.apply(second)

    assert result["applied"] is True
    assert manager.current is not None
    assert manager.current.generation == 2
    assert state.admission is not old_admission
    assert state.admission.snapshot()[0]["parallel_slots"] == 2
    assert len(manager.retired) == 1
    assert manager.retired[0].generation == 1
    assert manager.retired[0].admission is old_admission
    assert manager.retired[0].admission.snapshot()[0]["active"] == 1

    await lease.release()
    assert manager.status()["retired_generations"] == []

def test_enriched_ed25519_metadata_survives_consumer_and_tamper_is_rejected(
    monkeypatch,
):
    signer = Ed25519PrivateKey.generate()
    configure_key(monkeypatch, signer)
    admitted = provider()
    admitted.routing_roles = {"summarization"}
    admitted.worker_mode = "auxiliary"
    admitted.allow_agent_runtime = False
    admitted.allow_code_execution = False
    model = admitted.models[0]
    model.routing_roles = {"summarization"}
    model.worker_mode = "auxiliary"
    model.allow_agent_runtime = False
    model.allow_code_execution = False
    model.task_family_scores = {
        "summarization": {
            "quality_floor_passed": True,
            "utility_score": 0.8,
        }
    }
    payload = sign_document(signer, item=admitted)

    document, converted = validate_projection_document(payload, now_ms=1_010_000)
    parsed_provider = document.providers[0]
    parsed_model = parsed_provider.models[0]
    assert parsed_provider.routing_roles == {"summarization"}
    assert parsed_provider.worker_mode == "auxiliary"
    assert parsed_provider.allow_code_execution is False
    assert parsed_model.task_family_scores["summarization"]["utility_score"] == 0.8
    assert converted["providers"][0]["models"][0]["alias"] == "local/qwen"
    assert (
        converted["providers"][0]["models"][0]
        ["task_family_scores"]["summarization"]["utility_score"]
        == 0.8
    )
    assert len(document.providers) == 1
    assert len(parsed_provider.models) == 1

    def change_role(p):
        p["providers"][0]["routing_roles"] = ["full_agent"]

    def change_permission(p):
        p["providers"][0]["models"][0]["allow_code_execution"] = True

    def change_score(p):
        p["providers"][0]["models"][0]["task_family_scores"]["summarization"][
            "utility_score"
        ] = 1.0

    for tamper in (change_role, change_permission, change_score):
        altered = deepcopy(payload)
        tamper(altered)
        with pytest.raises(ValueError, match="checksum mismatch"):
            validate_projection_document(altered, now_ms=1_010_000)


@pytest.mark.asyncio
async def test_routing_metadata_change_requires_new_generation(monkeypatch):
    signer = Ed25519PrivateKey.generate()
    configure_key(monkeypatch, signer)
    install_manager_fixtures(monkeypatch)
    monkeypatch.setattr(legacy.time, "time", lambda: 1010.0)
    state = SimpleNamespace(agents=SimpleNamespace(), policies=SimpleNamespace())
    manager = RuntimeProjectionManager(state)

    approved = provider()
    approved.worker_mode = "auxiliary"
    approved.routing_roles = {"summarization"}
    approved.models[0].worker_mode = "auxiliary"
    initial = sign_document(signer, item=approved)
    assert (await manager.apply(initial))["applied"] is True
    assert manager.current is not None
    initial_checksum = manager.current.checksum

    # A signed metadata change is not a lease-only refresh of generation 1.
    denied = approved.model_copy(deep=True)
    denied.worker_mode = "observer_only"
    denied.routing_roles = set()
    denied.models[0].worker_mode = "observer_only"
    denied.models[0].routing_roles = set()
    same_generation = sign_document(signer, item=denied)
    with pytest.raises(ValueError, match="checksum conflict"):
        await manager.apply(same_generation)
    assert manager.current.generation == 1
    assert manager.current.checksum == initial_checksum

    next_generation = sign_document(
        signer, generation=2, item=denied,
        generated_at_ms=1_010_000, expires_at_ms=1_070_000,
    )
    result = await manager.apply(next_generation)
    assert result["applied"] is True
    assert manager.current.generation == 2
    assert state.providers.enabled()[0].worker_mode == "observer_only"
    assert state.providers.enabled()[0].models[0].worker_mode == "observer_only"
