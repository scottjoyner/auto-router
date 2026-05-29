from __future__ import annotations

from auto_router.config import PolicyRegistry, ProviderRegistry
from auto_router.models import (
    ExecutionPlan,
    ExecutionStage,
    ModelConfig,
    PolicyProfile,
    PolicyStage,
    Priority,
    ProviderCandidate,
    ProviderConfig,
    RouterRequest,
    StagePurpose,
)


class PolicyEngine:
    """Builds execution plans from request metadata and provider capabilities."""

    def __init__(self, providers: ProviderRegistry, policies: PolicyRegistry, default_profile: str):
        self.providers = providers
        self.policies = policies
        self.default_profile = default_profile

    def classify_profile(self, request: RouterRequest) -> str:
        if request.local_only or request.priority == Priority.local_only:
            return "local_only"

        metadata_profile = request.metadata.get("profile")
        if isinstance(metadata_profile, str) and metadata_profile in self.policies.profiles:
            return metadata_profile

        if request.priority == Priority.critical:
            return "high_priority_deliverable"
        if request.priority == Priority.repo_critical:
            return "code_high_quality"

        requested_model = request.model or ""
        if requested_model.startswith("auto/high"):
            return "high_priority_deliverable"
        if requested_model.startswith("auto/code"):
            return "code_high_quality"
        if requested_model.startswith("auto/local") or requested_model.startswith("auto/private"):
            return "local_only"

        return self.default_profile

    def plan(self, request: RouterRequest) -> ExecutionPlan:
        exact = self._exact_model_plan(request)
        if exact is not None:
            return exact

        profile_name = self.classify_profile(request)
        profile = self.policies.profiles.get(profile_name) or self._fallback_profile()
        stages = [self._build_stage(stage) for stage in profile.stages]
        return ExecutionPlan(profile_name=profile_name, stages=stages)

    def _exact_model_plan(self, request: RouterRequest) -> ExecutionPlan | None:
        requested_model = request.model or ""
        if not requested_model or requested_model.startswith("auto/"):
            return None

        for provider in self.providers.enabled():
            for model in provider.models:
                if requested_model not in {model.alias, model.provider_model}:
                    continue
                if request.local_only and str(provider.quota_class) != "local":
                    continue
                candidate = ProviderCandidate(
                    provider=provider,
                    model=model,
                    score=float(provider.priority),
                    reason="exact model alias match",
                )
                stage = ExecutionStage(
                    purpose=StagePurpose.final,
                    candidates=[candidate],
                    required_capabilities=request.required_capabilities,
                    allow_local_fallback=False,
                )
                return ExecutionPlan(profile_name="exact_model", stages=[stage])
        return None

    def _build_stage(self, policy_stage: PolicyStage) -> ExecutionStage:
        candidates: list[ProviderCandidate] = []
        for provider in self.providers.enabled():
            if policy_stage.provider_classes and str(provider.quota_class) not in policy_stage.provider_classes:
                continue
            for model in provider.models:
                if not self._model_matches(model, policy_stage.required_capabilities):
                    continue
                candidates.append(
                    ProviderCandidate(
                        provider=provider,
                        model=model,
                        score=self._score(provider, model, policy_stage),
                        reason=f"matched {policy_stage.purpose}",
                    )
                )

        candidates.sort(key=lambda candidate: candidate.score)
        return ExecutionStage(
            purpose=policy_stage.purpose,
            candidates=candidates,
            required_capabilities=policy_stage.required_capabilities,
            allow_local_fallback=policy_stage.allow_local_fallback,
            optional=policy_stage.optional,
        )

    def _model_matches(self, model: ModelConfig, required: set[str]) -> bool:
        if not required:
            return True
        return required.issubset(model.capabilities)

    def _score(self, provider: ProviderConfig, model: ModelConfig, stage: PolicyStage) -> float:
        score = float(provider.priority)
        if stage.purpose == StagePurpose.draft and str(provider.quota_class) == "local":
            score -= 50
        if stage.purpose in {StagePurpose.refine, StagePurpose.judge} and str(provider.quota_class) == "local":
            score += 100
        if "low_latency" in model.capabilities:
            score -= 5
        if "reasoning" in model.capabilities and stage.purpose in {StagePurpose.refine, StagePurpose.judge}:
            score -= 10
        return score

    def _fallback_profile(self) -> PolicyProfile:
        return PolicyProfile(
            description="Built-in local-only fallback profile.",
            stages=[
                PolicyStage(
                    purpose=StagePurpose.final,
                    provider_classes=["local"],
                    required_capabilities={"chat"},
                    allow_local_fallback=True,
                )
            ],
        )
