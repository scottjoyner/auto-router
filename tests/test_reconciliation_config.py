from pathlib import Path

from auto_router.config import load_provider_registry
from auto_router.offline_guard import validate_offline_provider_config


def test_reconciliation_registry_is_unroutable_bootstrap_and_offline() -> None:
    config = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "providers.reconciliation.yaml"
    )

    assert validate_offline_provider_config(config, env={}) == []

    registry = load_provider_registry(config)
    enabled = registry.enabled()
    assert len(enabled) == 1

    provider = enabled[0]
    assert provider.name == "reconciliation-bootstrap-unadmitted"
    assert provider.node_id == "bootstrap-unadmitted"
    assert provider.runtime_instance_id == "bootstrap-unadmitted"
    assert provider.runtime_kind == "openai_compatible"
    assert provider.runtime_version == "bootstrap"
    assert provider.parallel_slots == 0
    assert provider.queue_limit == 0
    assert provider.queue_timeout_seconds == 0
    assert provider.base_url == "http://127.0.0.1:9/v1"
    assert provider.access_urls == ["http://127.0.0.1:9/v1"]
    assert str(provider.quota_class) == "local"
    assert provider.gateway_managed is False
    assert provider.models == []
