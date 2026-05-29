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



def test_build_agent_job_request_defaults() -> None:
    from auto_router.agent_jobs import build_agent_job_request

    request = build_agent_job_request({"task": "review this repo"})

    assert request.job_id
    assert request.preferred_workers == []


def test_load_context_snapshot_bootstraps_registries(tmp_path: Path) -> None:
    from auto_router.config import AgentWorkerRegistry, ProviderRegistry, load_context_snapshot
    from auto_router.models import AgentWorkerConfig, ModelConfig, ProviderConfig

    context_path = tmp_path / "context.yaml"
    providers = ProviderRegistry(
        providers=[
            ProviderConfig(
                name="local",
                type="lmstudio",
                base_url="http://localhost:1234/v1",
                quota_class="local",
                models=[ModelConfig(alias="local/default", provider_model="local/default", capabilities={"chat"})],
            ),
            ProviderConfig(
                name="cloud",
                type="openai_compatible",
                base_url="https://example.com/v1",
                quota_class="fast_free",
                models=[ModelConfig(alias="cloud/model", provider_model="cloud/model", capabilities={"chat"})],
            ),
        ]
    )
    agents = AgentWorkerRegistry(
        agent_workers=[
            AgentWorkerConfig(name="codex", type="codex", command="codex", enabled=True),
        ]
    )

    snapshot = load_context_snapshot(context_path, providers, agents)

    assert snapshot.local_provider_names() == ["local"]
    assert snapshot.free_api_provider_names() == ["cloud"]
    assert snapshot.running_local_node_names() == ["codex"]



def test_context_provider_alias_lookup() -> None:
    from auto_router.context import ContextProvider, ContextSnapshot, ExecutionLane

    snapshot = ContextSnapshot(
        providers=[
            ContextProvider(
                provider="lm_studio",
                lane=ExecutionLane.local,
                local=True,
                can_use_free_api=False,
                aliases=["lmstudio-r2d2", "local/default"],
            )
        ]
    )

    assert snapshot.provider_for("lmstudio-r2d2").provider == "lm_studio"
    assert snapshot.provider_for("local/default").provider == "lm_studio"
