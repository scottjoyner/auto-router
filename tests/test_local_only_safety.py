"""P0.3: local-only enforcement and sensitive-marker privacy wall."""

import pytest

from auto_router import main as main_module
from auto_router.config import ContextSnapshot, PolicyRegistry, ProviderRegistry
from auto_router.models import (
    ModelConfig,
    PolicyProfile,
    PolicyStage,
    Priority,
    ProviderCandidate,
    ProviderConfig,
    RouterRequest,
    StagePurpose,
)
from auto_router.policy import PolicyEngine


def _engine() -> PolicyEngine:
    cloud = ProviderConfig(
        name="cloud",
        type="openai_compatible",
        base_url="https://example.com/v1",
        quota_class="fast_free",
        models=[
            ModelConfig(alias="cloud/chat", provider_model="cloud-chat", capabilities={"chat"})
        ],
    )
    local = ProviderConfig(
        name="local",
        type="lmstudio",
        base_url="http://localhost:1234/v1",
        quota_class="local",
        models=[
            ModelConfig(alias="local/chat", provider_model="local-chat", capabilities={"chat"})
        ],
    )
    stage = PolicyStage(purpose=StagePurpose.final, required_capabilities={"chat"})
    policies = PolicyRegistry(
        profiles={
            "local_only": PolicyProfile(stages=[stage]),
            "interactive_balanced": PolicyProfile(stages=[stage]),
        }
    )
    return PolicyEngine(
        ProviderRegistry(providers=[cloud, local]), policies, "interactive_balanced"
    )


def _cloud_candidate() -> ProviderCandidate:
    provider = ProviderConfig(
        name="cloud",
        type="openai_compatible",
        base_url="https://example.com/v1",
        quota_class="fast_free",
    )
    return ProviderCandidate(
        provider=provider,
        model=ModelConfig(alias="cloud/chat", provider_model="cloud-chat", capabilities={"chat"}),
    )


def _selected_providers(plan) -> list[str]:
    return [c.provider.name for stage in plan.stages for c in stage.candidates]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"local_only": True},
        {"priority": Priority.local_only},
        {"allow_cloud": False},
    ],
    ids=["local_only_flag", "priority_local_only", "allow_cloud_false"],
)
def test_explicit_local_only_request_never_selects_cloud(kwargs) -> None:
    plan = _engine().plan(RouterRequest(request_id="req-1", route="chat_completions", **kwargs))

    assert _selected_providers(plan) == ["local"]
    assert "cloud" not in _selected_providers(plan)


@pytest.mark.parametrize(
    "body",
    [
        {"metadata": {"local_only": True}},
        {"metadata": {"priority": "local_only"}},
        {"metadata": {"allow_cloud": False}},
        {"allow_cloud": False},
    ],
    ids=["metadata_local_only", "metadata_priority", "metadata_allow_cloud", "body_allow_cloud"],
)
def test_request_construction_keeps_explicit_local_only_off_cloud(body) -> None:
    request = main_module._router_request("chat_completions", body)

    assert main_module._candidate_allowed(request, _cloud_candidate(), ContextSnapshot()) is False


@pytest.mark.parametrize("marker", ["voice_auth", "enrollment_sample", "private_data", "secrets"])
@pytest.mark.parametrize(
    "placement", ["markers", "metadata_key"], ids=["marker_list", "metadata_key"]
)
def test_sensitive_marker_denies_cloud_routing(marker: str, placement: str) -> None:
    metadata = {"markers": [marker]} if placement == "markers" else {marker: True}
    engine = _engine()
    request = RouterRequest(request_id="req-1", route="chat_completions", metadata=metadata)

    plan = engine.plan(request)
    assert plan.profile_name == "local_only"
    assert _selected_providers(plan) == ["local"]

    gateway_request = main_module._router_request("chat_completions", {"metadata": metadata})
    assert gateway_request.local_only is True
    assert gateway_request.allow_cloud is False
    assert (
        main_module._candidate_allowed(gateway_request, _cloud_candidate(), ContextSnapshot())
        is False
    )


def test_exact_cloud_model_alias_cannot_bypass_allow_cloud_false() -> None:
    plan = _engine().plan(
        RouterRequest(
            request_id="req-1",
            route="chat_completions",
            model="cloud/chat",
            allow_cloud=False,
        )
    )

    assert "cloud" not in _selected_providers(plan)
