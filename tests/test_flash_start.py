from auto_router.config import PolicyRegistry, ProviderRegistry
from auto_router.models import ModelConfig, PolicyProfile, PolicyStage, ProviderConfig, RouterRequest
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


def test_flash_planning_model_gets_draft_priority_boost() -> None:
    providers = ProviderRegistry(
        providers=[
            ProviderConfig(
                name="normal-fast",
                type="openai_compatible",
                base_url="https://example.test/v1",
                quota_class="fast_free",
                priority=10,
                models=[ModelConfig(alias="normal", provider_model="normal", capabilities={"chat", "low_latency"})],
            ),
            ProviderConfig(
                name="cerebras",
                type="openai_compatible",
                base_url="https://example.test/v1",
                quota_class="fast_free",
                priority=10,
                models=[
                    ModelConfig(
                        alias="cerebras/flash-reasoner",
                        provider_model="gpt-oss-120b",
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
                        provider_classes=["fast_free"],
                        required_capabilities={"chat", "low_latency"},
                    )
                ]
            )
        }
    )
    engine = PolicyEngine(providers, policies, "flash_start_planner")
    request = RouterRequest(request_id="1", route="chat_completions", model="auto/flash-start")

    plan = engine.plan(request)

    assert plan.profile_name == "flash_start_planner"
    assert plan.stages[0].candidates[0].provider.name == "cerebras"
