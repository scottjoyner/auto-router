from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_router_image_and_reconciliation_compose_use_secure_entrypoint() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.reconciliation.yml").read_text(encoding="utf-8")

    assert "auto_router.secure_live:app" in dockerfile
    assert "auto_router.main_live:app" not in dockerfile
    assert "auto_router.secure_live:app" in compose
    assert '"127.0.0.1:${RECONCILIATION_ROUTER_PORT:-18088}:8088"' in compose
    assert '"0.0.0.0:${RECONCILIATION_ROUTER_PORT' not in compose


def test_reconciliation_compose_requires_separate_executor_and_projection_keys() -> None:
    compose = (ROOT / "compose.reconciliation.yml").read_text(encoding="utf-8")
    environment = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "AUTO_ROUTER_EXECUTOR_VERIFY_KEY_FILE" in compose
    assert "assistx_executor_public_key.pem" in compose
    assert "AUTO_ROUTER_RUNTIME_PROJECTION_VERIFY_KEY_FILE" in compose
    assert "assistx_runtime_projection_public_key.pem" in compose
    assert "ASSISTX_EXECUTOR_PUBLIC_KEY_FILE" in compose
    assert "ASSISTX_RUNTIME_PROJECTION_PUBLIC_KEY_FILE" in compose
    assert "AUTO_ROUTER_ASSISTX_EXECUTOR_SERVICE_TOKEN" in compose
    assert "AUTO_ROUTER_INTERNAL_SERVICE_TOKEN" in compose
    assert "AUTO_ROUTER_RUNTIME_PROJECTION_HMAC_SECRET" not in compose
    assert "AUTO_ROUTER_RUNTIME_PROJECTION_HMAC_SECRET" not in environment


def test_reconciliation_network_and_runtime_identity_are_explicit() -> None:
    compose = (ROOT / "compose.reconciliation.yml").read_text(encoding="utf-8")

    assert "assistx_reconciliation_shared" in compose
    assert "external: true" in compose
    for name in (
        "RECONCILIATION_RUNTIME_INSTANCE_ID",
        "RECONCILIATION_RUNTIME_VERSION",
        "RECONCILIATION_MODEL_INSTANCE_ID",
        "RECONCILIATION_MODEL_ARTIFACT_FINGERPRINT",
        "RECONCILIATION_MODEL_QUANTIZATION",
        "RECONCILIATION_PARALLEL_SLOTS",
    ):
        assert name in compose
