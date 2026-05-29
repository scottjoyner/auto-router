from auto_router.config import PolicyRegistry, ProviderRegistry
from auto_router.models import ModelConfig, PolicyProfile, PolicyStage, ProviderConfig, RouterRequest
from auto_router.policy import PolicyEngine


def test_local_only_request_routes_only_to_local() -> None:
    providers = ProviderRegistry(
        providers=[
            ProviderConfig(
                name="cloud",
                type="openai_compatible",
                base_url="https://example.com/v1",
                quota_class="fast_free",
                models=[ModelConfig(alias="cloud/model", provider_model="cloud-model", capabilities={"chat"})],
            ),
            ProviderConfig(
                name="local",
                type="lmstudio",
                base_url="http://localhost:1234/v1",
                quota_class="local",
                models=[ModelConfig(alias="local/model", provider_model="local-model", capabilities={"chat"})],
            ),
        ]
    )
    policies = PolicyRegistry(
        profiles={
            "local_only": PolicyProfile(
                stages=[
                    PolicyStage(
                        purpose="final",
                        provider_classes=["local"],
                        required_capabilities={"chat"},
                    )
                ]
            )
        }
    )
    engine = PolicyEngine(providers, policies, "local_only")
    request = RouterRequest(request_id="1", route="chat_completions", local_only=True)

    plan = engine.plan(request)

    assert plan.profile_name == "local_only"
    assert len(plan.stages[0].candidates) == 1
    assert plan.stages[0].candidates[0].provider.name == "local"


def test_high_priority_uses_high_priority_profile() -> None:
    providers = ProviderRegistry(providers=[])
    policies = PolicyRegistry(
        profiles={
            "high_priority_deliverable": PolicyProfile(stages=[]),
            "interactive_balanced": PolicyProfile(stages=[]),
        }
    )
    engine = PolicyEngine(providers, policies, "interactive_balanced")
    request = RouterRequest(request_id="1", route="chat_completions", priority="critical")

    assert engine.classify_profile(request) == "high_priority_deliverable"


def test_exact_model_alias_is_honored() -> None:
    providers = ProviderRegistry(
        providers=[
            ProviderConfig(
                name="groq",
                type="openai_compatible",
                base_url="https://example.com/v1",
                quota_class="fast_free",
                models=[ModelConfig(alias="groq/fast", provider_model="llama", capabilities={"chat"})],
            )
        ]
    )
    policies = PolicyRegistry(profiles={"interactive_balanced": PolicyProfile(stages=[])})
    engine = PolicyEngine(providers, policies, "interactive_balanced")
    request = RouterRequest(request_id="1", route="chat_completions", model="groq/fast")

    plan = engine.plan(request)

    assert plan.profile_name == "exact_model"
    assert plan.stages[0].candidates[0].provider.name == "groq"
    assert plan.stages[0].candidates[0].model.provider_model == "llama"


def test_blocked_context_provider_is_skipped() -> None:
    from auto_router.context import ContextProvider, ContextSnapshot, ExecutionLane

    providers = ProviderRegistry(
        providers=[
            ProviderConfig(
                name="cloud",
                type="openai_compatible",
                base_url="https://example.com/v1",
                quota_class="fast_free",
                models=[ModelConfig(alias="cloud/model", provider_model="cloud-model", capabilities={"chat"})],
            ),
            ProviderConfig(
                name="local",
                type="lmstudio",
                base_url="http://localhost:1234/v1",
                quota_class="local",
                models=[ModelConfig(alias="local/model", provider_model="local-model", capabilities={"chat"})],
            ),
        ]
    )
    policies = PolicyRegistry(
        profiles={
            "interactive_balanced": PolicyProfile(
                stages=[
                    PolicyStage(
                        purpose="final",
                        provider_classes=[],
                        required_capabilities={"chat"},
                    )
                ]
            )
        }
    )
    context = ContextSnapshot(
        providers=[
            ContextProvider(
                provider="cloud",
                lane=ExecutionLane.blocked,
                local=False,
                can_use_free_api=False,
                blocked=True,
            ),
            ContextProvider(provider="local", lane=ExecutionLane.local, local=True, can_use_free_api=False),
        ]
    )
    engine = PolicyEngine(providers, policies, "interactive_balanced", context)
    request = RouterRequest(request_id="1", route="chat_completions")

    plan = engine.plan(request)

    assert [candidate.provider.name for candidate in plan.stages[0].candidates] == ["local"]


def test_auto_sophia_model_uses_sophia_realtime_profile() -> None:
    providers = ProviderRegistry(providers=[])
    policies = PolicyRegistry(
        profiles={
            "sophia_realtime": PolicyProfile(stages=[]),
            "interactive_balanced": PolicyProfile(stages=[]),
        }
    )
    engine = PolicyEngine(providers, policies, "interactive_balanced")
    request = RouterRequest(request_id="1", route="chat_completions", model="auto/sophia")

    assert engine.classify_profile(request) == "sophia_realtime"


def test_auto_backlog_model_uses_backlog_burn_profile() -> None:
    providers = ProviderRegistry(providers=[])
    policies = PolicyRegistry(
        profiles={
            "backlog_burn": PolicyProfile(stages=[]),
            "interactive_balanced": PolicyProfile(stages=[]),
        }
    )
    engine = PolicyEngine(providers, policies, "interactive_balanced")
    request = RouterRequest(request_id="1", route="chat_completions", model="auto/backlog-burn")

    assert engine.classify_profile(request) == "backlog_burn"
