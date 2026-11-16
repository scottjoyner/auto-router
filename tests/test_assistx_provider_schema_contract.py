"""Source-contract regression for AssistX control-room provider discovery.

Grounded against reviewed AssistX control_room._provider_catalog source
at 7a12e7bd4831b04387c802b7abbe7b8db6ed6be0. These are synthetic,
offline shape checks, not a live client or operator authorization test.
"""
from pathlib import Path
from types import SimpleNamespace

import yaml

from auto_router import main as router_main


ROOT = Path(__file__).resolve().parents[1]


def test_redacted_inventory_retains_stable_assistx_identity_and_model_alias():
    fixture = {
        "id": "synthetic-only",
        "name": "fixture",
        "enabled": True,
        "node_id": "synthetic-node",
        "runtime_instance_id": "synthetic-runtime",
        "models": [{
            "alias": "model-alias",
            "provider_model": "synthetic/model",
            "context_window": 2048,
            "secret_future_key": "SECRET_NESTED",
        }],
        "base_url": "https://user:SECRET_PASS@example.invalid/?key=SECRET_QUERY",
        "access_urls": ["https://SECRET_URL.invalid/?token=SECRET_VALUE"],
        "api_key_env": "SECRET_ENV",
        "api_key_file": "/private/SECRET_FILE",
        "headers": {"Authorization": "SECRET_AUTH"},
    }
    result = router_main._redacted_admin_provider(
        SimpleNamespace(model_dump=lambda: fixture)
    )
    assert result["enabled"] is True
    assert result["node_id"] == "synthetic-node"
    assert result["runtime_instance_id"] == "synthetic-runtime"
    assert result["models"][0]["provider_model"] == "synthetic/model"
    assert result["models"][0]["alias"] == "model-alias"
    assert "base_url" not in result
    assert "headers" not in result
    assert "api_key_env" not in result
    assert "api_key_file" not in result
    assert "access_urls" not in result
    assert "SECRET_" not in str(result)


def test_configured_provider_catalog_retains_stable_identity_after_redaction():
    # The known AssistX code uses provider.node_id / runtime_instance_id,
    # or falls back to host parsed from base_url. We MUST NOT expose base_url
    # merely to preserve that fallback: all configured entries need node_id.
    data = yaml.safe_load((ROOT / "config" / "providers.yaml").read_text())
    assert isinstance(data, dict)
    providers = data.get("providers")
    assert isinstance(providers, list) and providers
    for provider in providers:
        assert isinstance(provider, dict)
        assert isinstance(provider.get("node_id"), str)
        assert provider["node_id"].strip()
        assert type(provider.get("enabled")) is bool
        models = provider.get("models", [])
        assert isinstance(models, list)
        for model in models:
            assert isinstance(model, dict)
            assert model.get("provider_model") or model.get("alias")
        # This checks the same security allowlist used by the API but never
        # opens a socket, parses credential values or prints raw config.
        redacted = router_main._redacted_admin_provider(
            SimpleNamespace(model_dump=lambda: provider)
        )
        assert redacted["node_id"] == provider["node_id"]
        assert "base_url" not in redacted
        assert all(
            row.get("provider_model") or row.get("alias")
            for row in redacted["models"]
        )
