from auto_router.config import AgentWorkerRegistry, ProviderRegistry, load_context_snapshot
from auto_router.context import ContextModel, ContextProvider, ContextService, ContextSnapshot, ServiceStatus


def test_context_snapshot_collects_top_level_node_and_provider_services() -> None:
    snapshot = ContextSnapshot.model_validate(
        {
            "services": [
                {
                    "service_id": "auto-router.dashboard",
                    "name": "Auto Router Dashboard",
                    "url": "http://localhost:8088/dashboard",
                    "priority": 10,
                }
            ],
            "nodes": [
                {
                    "node_id": "deathstar",
                    "services": [
                        {
                            "service_id": "deathstar.neo4j",
                            "name": "Neo4j Browser",
                            "url": "http://deathstar-XPS-8920:7474",
                            "node_id": "deathstar",
                            "priority": 20,
                        }
                    ],
                }
            ],
            "providers": [
                {
                    "provider": "cerebras",
                    "services": [
                        {
                            "service_id": "cerebras.api",
                            "name": "Cerebras API",
                            "url": "https://api.cerebras.ai/v1",
                            "provider": "cerebras",
                            "status": "online",
                            "priority": 5,
                        }
                    ],
                }
            ],
        }
    )

    services = snapshot.all_services()

    assert [service.service_id for service in services] == [
        "cerebras.api",
        "auto-router.dashboard",
        "deathstar.neo4j",
    ]
    assert snapshot.services_for_provider("cerebras")[0].status == ServiceStatus.online
    assert snapshot.services_for_node("deathstar")[0].url.endswith(":7474")




def test_load_context_snapshot_bootstraps_provider_services(tmp_path) -> None:
    providers = ProviderRegistry.model_validate(
        {
            "providers": [
                {
                    "name": "cerebras",
                    "type": "openai_compatible",
                    "enabled": True,
                    "base_url": "https://api.cerebras.ai/v1",
                    "priority": 10,
                    "quota_class": "fast_free",
                    "models": [
                        {
                            "alias": "cerebras/flash-reasoner",
                            "provider_model": "gpt-oss-120b",
                            "capabilities": ["chat", "streaming", "flash_planning"],
                        }
                    ],
                }
            ]
        }
    )
    agents = AgentWorkerRegistry.model_validate({"agent_workers": []})

    snapshot = load_context_snapshot(tmp_path / "assistx-context.yaml", providers, agents)

    service = snapshot.services_for_provider("cerebras")[0]
    provider = next(item for item in snapshot.providers if item.provider == "cerebras")

    assert service.service_id == "provider.cerebras.api"
    assert service.health_url == "https://api.cerebras.ai/v1/models"
    assert service.provider == "cerebras"
    assert "cerebras/flash-reasoner" in provider.aliases
    assert "models:" in provider.detail
    assert any(item.service_id == "provider.cerebras.api" for item in snapshot.services)
    model = snapshot.model_for("cerebras.cerebras/flash-reasoner")
    assert model is not None
    assert model.provider == "cerebras"
    assert snapshot.models_for_provider("cerebras")[0].provider_model == "gpt-oss-120b"
    assert any(item.model_id == "cerebras.gpt-oss-120b" for item in snapshot.all_models())
    graph_summary = snapshot.graph_object_summary()
    assert graph_summary["provider"] == 1
    assert graph_summary["model"] >= 1
    assert graph_summary["service"] >= 1
    assert graph_summary["total"] == len(snapshot.graph_objects())


def test_load_context_snapshot_projects_graph_objects(tmp_path) -> None:
    payload = tmp_path / "assistx-context.yaml"
    payload.write_text(
        """revision: assistx
source: http://assistx/api/router/context-projection
graph_objects:
  - kind: provider
    id: lmstudio-r2d2
    properties:
      node_id: x1-370
      type: lmstudio
      detail: AssistX provider envelope
      aliases: [r2d2-lm]
  - kind: model
    id: lmstudio-r2d2.local/default
    properties:
      provider: lmstudio-r2d2
      provider_model: local/default
      name: local/default
      detail: Live LM Studio model
      capabilities: [chat, streaming]
      node_id: x1-370
  - kind: service
    id: provider.lmstudio-r2d2.api
    properties:
      provider: lmstudio-r2d2
      node_id: x1-370
      name: LM Studio API
      url: http://r2d2:1234/v1
      status: online
  - kind: signal
    id: provider.lmstudio-r2d2.preferred
    properties:
      target_type: provider
      target_id: lmstudio-r2d2
      signal_type: preferred
      strength: 2
      source: sophia
      detail: Prefer the local gateway-backed endpoint
""",
        encoding="utf-8",
    )

    snapshot = load_context_snapshot(
        payload,
        ProviderRegistry.model_validate({"providers": []}),
        AgentWorkerRegistry.model_validate({"agent_workers": []}),
    )

    provider = snapshot.provider_for("lmstudio-r2d2")
    model = snapshot.model_for("lmstudio-r2d2.local/default")
    services = snapshot.services_for_provider("lmstudio-r2d2")

    assert provider is not None
    assert provider.detail == "AssistX provider envelope"
    assert "r2d2-lm" in provider.aliases
    assert model is not None
    assert model.provider == "lmstudio-r2d2"
    assert model.provider_model == "local/default"
    assert services[0].status == ServiceStatus.online
    assert snapshot.signal_summary()["total"] == 1
    assert snapshot.signals_for_provider("lmstudio-r2d2")[0].signal_type == "preferred"
    graph_summary = snapshot.graph_object_summary()
    assert graph_summary["provider"] >= 1
    assert graph_summary["model"] >= 1
    assert graph_summary["service"] >= 1
    assert graph_summary["signal"] == 1


def test_load_context_snapshot_marks_bootstrap_fallback_when_http_projection_unreachable(monkeypatch) -> None:
    from auto_router import config as config_module

    def fail(*_args, **_kwargs):
        raise RuntimeError("projection unavailable")

    monkeypatch.setattr(config_module, "_load_json_source", fail)

    snapshot = load_context_snapshot(
        "http://assistx:8000/api/router/context-projection",
        ProviderRegistry.model_validate({"providers": []}),
        AgentWorkerRegistry.model_validate({"agent_workers": []}),
    )

    assert snapshot.projection_status() == "bootstrap_fallback"
    assert snapshot.is_projection_degraded() is True
    assert snapshot.projection_error()


def test_context_snapshot_deduplicates_services_by_id_with_nested_precedence() -> None:
    snapshot = ContextSnapshot(
        services=[
            ContextService(
                service_id="shared",
                name="Top Level",
                url="http://top.example",
                priority=50,
            )
        ],
        providers=[
            ContextProvider(
                provider="cerebras",
                services=[
                    ContextService(
                        service_id="shared",
                        name="Provider Level",
                        url="http://provider.example",
                        provider="cerebras",
                        priority=5,
                    )
                ],
            )
        ],
    )

    services = snapshot.all_services()

    assert len(services) == 1
    assert services[0].name == "Provider Level"
    assert services[0].provider == "cerebras"
