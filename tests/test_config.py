from pathlib import Path

from auto_router.config import load_policy_registry, load_provider_registry


def test_load_provider_registry(tmp_path: Path) -> None:
    config = tmp_path / "providers.yaml"
    config.write_text(
        """
providers:
  - name: local
    type: lmstudio
    enabled: true
    base_url: http://localhost:1234/v1
    quota_class: local
    models:
      - alias: local/default
        provider_model: local/default
        capabilities: [chat, code]
""",
        encoding="utf-8",
    )

    registry = load_provider_registry(config)

    assert len(registry.providers) == 1
    assert registry.providers[0].models[0].alias == "local/default"
    assert "chat" in registry.providers[0].models[0].capabilities


def test_load_policy_registry(tmp_path: Path) -> None:
    config = tmp_path / "policies.yaml"
    config.write_text(
        """
profiles:
  local_only:
    stages:
      - purpose: final
        provider_classes: [local]
        required_capabilities: [chat]
classification:
  default_profile: local_only
""",
        encoding="utf-8",
    )

    registry = load_policy_registry(config)

    assert "local_only" in registry.profiles
    assert registry.profiles["local_only"].stages[0].purpose == "final"
