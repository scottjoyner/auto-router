from auto_router.context import ContextService, ContextSnapshot, ServiceStatus


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
            {
                "provider": "cerebras",
                "services": [
                    {
                        "service_id": "shared",
                        "name": "Provider Level",
                        "url": "http://provider.example",
                        "provider": "cerebras",
                        "priority": 5,
                    }
                ],
            }
        ],
    )

    services = snapshot.all_services()

    assert len(services) == 1
    assert services[0].name == "Provider Level"
    assert services[0].provider == "cerebras"
