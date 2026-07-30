from auto_router.config import PolicyRegistry, ProviderRegistry
from auto_router.models import (
    ModelConfig,
    PolicyProfile,
    PolicyStage,
    ProviderConfig,
    RouterRequest,
)
from auto_router.policy import PolicyEngine


def test_auto_flash_start_model_uses_flash_start_profile() -> None:
    policies = PolicyRegistry(
        profiles={
            "flash_start_planner": PolicyProfile(stages=[]),
            "interactive_balanced": PolicyProfile(stages=[]),
        }
    )
    engine = PolicyEngine(ProviderRegistry(providers=[]), policies, "interactive_balanced")
    request = RouterRequest(request_id="1", route="chat_completions", model="auto/flash-start")

    assert engine.classify_profile(request) == "flash_start_planner"


def test_flash_planning_alias_selects_capable_local_model() -> None:
    providers = ProviderRegistry(
        providers=[
            ProviderConfig(
                name="local-normal",
                type="lmstudio",
                node_id="normal-node",
                base_url="http://normal-node:1234/v1",
                quota_class="local",
                priority=20,
                models=[
                    ModelConfig(
                        alias="local/normal",
                        provider_model="normal-7b",
                        capabilities={"chat", "low_latency"},
                    )
                ],
            ),
            ProviderConfig(
                name="local-flash-planner",
                type="lmstudio",
                node_id="planner-node",
                base_url="http://planner-node:1234/v1",
                quota_class="local",
                priority=10,
                models=[
                    ModelConfig(
                        alias="local/flash-planner",
                        provider_model="planner-7b",
                        capabilities={"chat", "low_latency", "flash_planning"},
                    )
                ],
            ),
        ]
    )
    policies = PolicyRegistry(
        profiles={
            "flash_start_planner": PolicyProfile(
                stages=[
                    PolicyStage(
                        purpose="draft",
                        provider_classes=["local"],
                        required_capabilities={"chat", "low_latency", "flash_planning"},
                    )
                ]
            )
        }
    )
    engine = PolicyEngine(providers, policies, "flash_start_planner")
    request = RouterRequest(request_id="1", route="chat_completions", model="auto/flash-start")

    plan = engine.plan(request)

    assert plan.profile_name == "flash_start_planner"
    assert len(plan.stages[0].candidates) == 1
    candidate = plan.stages[0].candidates[0]
    assert candidate.provider.name == "local-flash-planner"
    assert candidate.provider.quota_class == "local"
    assert "flash_planning" in candidate.model.capabilities
