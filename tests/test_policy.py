from auto_router.config import PolicyRegistry, ProviderRegistry
from auto_router.models import ModelConfig, PolicyProfile, PolicyStage, ProviderConfig, RouterRequest, StagePurpose
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


def test_signal_preference_boosts_provider_selection() -> None:
    from auto_router.context import ContextSignal, ContextSnapshot

    providers = ProviderRegistry(
        providers=[
            ProviderConfig(
                name="slow-cloud",
                type="openai_compatible",
                base_url="https://slow.example/v1",
                quota_class="fast_free",
                priority=100,
                models=[ModelConfig(alias="slow/model", provider_model="slow-model", capabilities={"chat"})],
            ),
            ProviderConfig(
                name="fast-cloud",
                type="openai_compatible",
                base_url="https://fast.example/v1",
                quota_class="fast_free",
                priority=90,
                models=[ModelConfig(alias="fast/model", provider_model="fast-model", capabilities={"chat"})],
            ),
        ]
    )
    policies = PolicyRegistry(
        profiles={
            "interactive_balanced": PolicyProfile(
                stages=[PolicyStage(purpose=StagePurpose.final, required_capabilities={"chat"})]
            )
        }
    )
    context = ContextSnapshot(
        providers=[],
        signals=[
            ContextSignal(
                signal_id="provider.slow-cloud.preferred",
                target_type="provider",
                target_id="slow-cloud",
                signal_type="preferred",
                source="sophia",
                strength=2.0,
                detail="Prefer slow-cloud for this workflow",
            )
        ],
    )
    engine = PolicyEngine(providers, policies, "interactive_balanced", context)
    request = RouterRequest(request_id="1", route="chat_completions")

    plan = engine.plan(request)

    assert plan.stages[0].candidates[0].provider.name == "slow-cloud"



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


def test_assistx_task_metadata_uses_backlog_profile() -> None:
    providers = ProviderRegistry(providers=[])
    policies = PolicyRegistry(
        profiles={
            "backlog_burn": PolicyProfile(stages=[]),
            "interactive_balanced": PolicyProfile(stages=[]),
        }
    )
    engine = PolicyEngine(providers, policies, "interactive_balanced")
    request = RouterRequest(
        request_id="1",
        route="chat_completions",
        metadata={"task_id": "assistx-task-1", "assistx_source": True},
    )

    assert engine.classify_profile(request) == "backlog_burn"


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


def test_private_metadata_forces_local_only_profile_and_candidates() -> None:
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
            "local_only": PolicyProfile(stages=[PolicyStage(purpose=StagePurpose.final, required_capabilities={"chat"})]),
            "interactive_balanced": PolicyProfile(stages=[PolicyStage(purpose=StagePurpose.final, required_capabilities={"chat"})]),
        }
    )
    engine = PolicyEngine(providers, policies, "interactive_balanced")
    request = RouterRequest(request_id="1", route="chat_completions", metadata={"privacy": "private"})

    plan = engine.plan(request)

    assert plan.profile_name == "local_only"
    assert [candidate.provider.name for candidate in plan.stages[0].candidates] == ["local"]


def test_auto_sophia_keeps_sophia_profile_but_excludes_cloud() -> None:
    providers = ProviderRegistry(
        providers=[
            ProviderConfig(
                name="cloud-fast",
                type="openai_compatible",
                base_url="https://example.com/v1",
                quota_class="fast_free",
                models=[ModelConfig(alias="cloud/fast", provider_model="cloud-fast", capabilities={"chat", "low_latency"})],
            ),
            ProviderConfig(
                name="local-fast",
                type="lmstudio",
                base_url="http://localhost:1234/v1",
                quota_class="local",
                models=[ModelConfig(alias="local/fast", provider_model="local-fast", capabilities={"chat", "low_latency"})],
            ),
        ]
    )
    policies = PolicyRegistry(
        profiles={
            "sophia_realtime": PolicyProfile(stages=[PolicyStage(purpose=StagePurpose.final, required_capabilities={"chat", "low_latency"})]),
            "local_only": PolicyProfile(stages=[PolicyStage(purpose=StagePurpose.final, required_capabilities={"chat"})]),
        }
    )
    engine = PolicyEngine(providers, policies, "local_only")
    request = RouterRequest(request_id="1", route="chat_completions", model="auto/sophia")

    plan = engine.plan(request)

    assert plan.profile_name == "sophia_realtime"
    assert [candidate.provider.name for candidate in plan.stages[0].candidates] == ["local-fast"]


def test_node_capabilities_bias_large_reasoning_to_x1_style_node() -> None:
    from auto_router.context import ContextNode, ContextProvider, ContextSnapshot, ExecutionLane

    providers = ProviderRegistry(
        providers=[
            ProviderConfig(
                name="lmstudio-x1",
                type="lmstudio",
                node_id="x1-370",
                base_url="http://x1:1234/v1",
                quota_class="local",
                priority=100,
                models=[ModelConfig(alias="local/reason", provider_model="reason", capabilities={"chat", "reasoning", "large_context"})],
            ),
            ProviderConfig(
                name="lmstudio-gpu",
                type="lmstudio",
                node_id="deathstar-XPS-8920",
                base_url="http://deathstar:1234/v1",
                quota_class="local",
                priority=100,
                models=[ModelConfig(alias="local/gpu", provider_model="gpu", capabilities={"chat", "reasoning", "large_context"})],
            ),
        ]
    )
    policies = PolicyRegistry(
        profiles={"interactive_balanced": PolicyProfile(stages=[PolicyStage(purpose=StagePurpose.final, required_capabilities={"chat"})])}
    )
    context = ContextSnapshot(
        nodes=[
            ContextNode(node_id="x1-370", capabilities={"large_models", "long_context"}),
            ContextNode(node_id="deathstar-XPS-8920", capabilities={"gpu_accelerated", "fast_inference"}),
        ],
        providers=[
            ContextProvider(provider="lmstudio-x1", lane=ExecutionLane.local, local=True, node_id="x1-370"),
            ContextProvider(provider="lmstudio-gpu", lane=ExecutionLane.local, local=True, node_id="deathstar-XPS-8920"),
        ],
    )
    engine = PolicyEngine(providers, policies, "interactive_balanced", context)

    plan = engine.plan(RouterRequest(request_id="1", route="chat_completions"))

    assert plan.stages[0].candidates[0].provider.name == "lmstudio-x1"
