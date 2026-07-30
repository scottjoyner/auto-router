from pathlib import Path

from auto_router.config import load_provider_registry
from auto_router.offline_guard import validate_offline_provider_config


def test_reconciliation_registry_is_single_runtime_and_offline() -> None:
    config = Path(__file__).resolve().parents[1] / "config" / "providers.reconciliation.yaml"
    env = {
        "RECONCILIATION_RUNTIME_NODE_ID": "x1-370",
        "RECONCILIATION_RUNTIME_INSTANCE_ID": "lmstudio-x1-370-1234",
        "RECONCILIATION_PARALLEL_SLOTS": "1",
        "RECONCILIATION_QUEUE_LIMIT": "4",
        "RECONCILIATION_QUEUE_TIMEOUT_SECONDS": "30",
        "RECONCILIATION_LAN_BASE_URL": "http://192.168.1.50:1234/v1",
        "RECONCILIATION_LMSTUDIO_BASE_URL": "http://host.docker.internal:1234/v1",
        "RECONCILIATION_TAILSCALE_BASE_URL": "http://100.64.43.123:1234/v1",
        "RECONCILIATION_MODEL_ID": "refinedtoolcallv5-3b",
        "RECONCILIATION_CONTEXT_WINDOW": "32768",
    }

    assert validate_offline_provider_config(config, env=env) == []

    registry = load_provider_registry(config)
    enabled = registry.enabled()
    assert len(enabled) == 1

    provider = enabled[0]
    assert provider.name == "reconciliation-local-runtime"
    assert provider.node_id in {"${RECONCILIATION_RUNTIME_NODE_ID:-x1-370}", "x1-370"}
    assert provider.runtime_instance_id == "lmstudio-reconciliation-1234"
    assert provider.parallel_slots == 1
    assert provider.queue_limit == 4
    assert provider.queue_timeout_seconds == 30
    assert "http://host.docker.internal:1234/v1" in provider.access_urls
    assert str(provider.quota_class) == "local"
    assert provider.gateway_managed is False
    assert provider.models[0].alias == "local/reconciliation-default"
