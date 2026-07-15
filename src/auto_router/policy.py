from __future__ import annotations

import threading
import time

from auto_router.config import PolicyRegistry, ProviderRegistry
from auto_router.context import ContextProvider, ContextSnapshot, ExecutionLane, ContextSignal
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
    def __init__(
        self,
        providers: ProviderRegistry,
        policies: PolicyRegistry,
        default_profile: str,
        context: ContextSnapshot | None = None,
    ):
        self.providers = providers
        self.policies = policies
        self.default_profile = default_profile
        self.context = context or ContextSnapshot()
        self._request_context: RouterRequest | None = None
        # Fleet-wide load balancing: track per-node last-used + in-flight so the
        # router spreads `auto` requests across the nodes that host a model instead
        # of hammering the single highest-priority node while peers sit idle.
        self._lb_last_used: dict[str, float] = {}
        self._lb_inflight: dict[str, int] = {}
        self._lb_lock = threading.Lock()

    def classify_profile(self, request: RouterRequest) -> str:
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        requested_model = request.model or ""
        workflow_stage = str(metadata.get("workflow_stage") or metadata.get("stage") or "").strip().lower()
        if self._request_requires_local_execution(request) and requested_model.startswith("auto/sophia") and "sophia_realtime" in self.policies.profiles:
            return "sophia_realtime"
        metadata_profile = metadata.get("profile")
        if isinstance(metadata_profile, str) and metadata_profile in self.policies.profiles:
            return metadata_profile
        if workflow_stage in {"handoff", "final", "finalized", "review_final"} and "iterative_review_handoff" in self.policies.profiles:
            return "iterative_review_handoff"
        if self._request_requires_local_execution(request):
            return "local_only"
        if (
            isinstance(metadata.get("task_id"), str)
            or bool(metadata.get("assistx_source"))
            or isinstance(request.task_id, str)
            or bool(request.agent_run_id)
        ) and "backlog_burn" in self.policies.profiles:
            return "backlog_burn"
        if request.priority == Priority.critical:
            return "high_priority_deliverable"
        if request.priority == Priority.repo_critical:
            return "code_high_quality"
        requested_model = request.model or ""
        if requested_model.startswith("auto/flash") and "flash_start_planner" in self.policies.profiles:
            return "flash_start_planner"
        if requested_model.startswith("auto/high"):
            return "high_priority_deliverable"
        if requested_model.startswith("auto/code"):
            return "code_high_quality"
        if requested_model.startswith("auto/review") and "iterative_review_handoff" in self.policies.profiles:
            return "iterative_review_handoff"
        if requested_model.startswith("auto/sophia") and "sophia_realtime" in self.policies.profiles:
            return "sophia_realtime"
        if requested_model.startswith("auto/backlog") and "backlog_burn" in self.policies.profiles:
            return "backlog_burn"
        if requested_model.startswith("auto/local") or requested_model.startswith("auto/private"):
            return "local_only"
        if requested_model.startswith("auto/iterate") and "iterative_review_handoff" in self.policies.profiles:
            return "iterative_review_handoff"
        return self.default_profile

    def plan(self, request: RouterRequest) -> ExecutionPlan:
        exact = self._exact_model_plan(request)
        if exact is not None:
            return exact
        profile_name = self.classify_profile(request)
        profile = self.policies.profiles.get(profile_name) or self._fallback_profile()
        stages = [self._build_stage(stage, request) for stage in profile.stages]
        return ExecutionPlan(profile_name=profile_name, stages=stages)

    def _exact_model_plan(self, request: RouterRequest) -> ExecutionPlan | None:
        requested_model = request.model or ""
        if not requested_model or requested_model.startswith("auto/"):
            return None
        for provider in self.providers.enabled():
            if not self._provider_is_eligible(provider, request):
                continue
            for model in provider.models:
                if requested_model not in {model.alias, model.provider_model}:
                    continue
                if not self._model_matches(model, request.required_capabilities):
                    continue
                candidate = ProviderCandidate(
                    provider=provider,
                    model=model,
                    score=float(provider.priority),
                    reason="exact model alias match",
                )
                return ExecutionPlan(
                    profile_name="exact_model",
                    stages=[
                        ExecutionStage(
                            purpose=StagePurpose.final,
                            candidates=[candidate],
                            required_capabilities=request.required_capabilities,
                            allow_local_fallback=False,
                        )
                    ],
                )
        return None

    def _build_stage(self, policy_stage: PolicyStage, request: RouterRequest) -> ExecutionStage:
        candidates: list[ProviderCandidate] = []
        for provider in self.providers.enabled():
            if not self._provider_is_eligible(provider, request):
                continue
            if policy_stage.provider_classes and str(provider.quota_class) not in policy_stage.provider_classes:
                continue
            for model in provider.models:
                if not self._model_matches(model, policy_stage.required_capabilities):
                    continue
                candidates.append(
                    ProviderCandidate(
                        provider=provider,
                        model=model,
                        score=self._score(provider, model, policy_stage, request),
                        reason=f"matched {policy_stage.purpose}",
                    )
                )
        candidates.sort(key=lambda candidate: candidate.score)
        candidates = self._balance_candidates(candidates)
        return ExecutionStage(
            purpose=policy_stage.purpose,
            candidates=candidates,
            required_capabilities=policy_stage.required_capabilities,
            allow_local_fallback=policy_stage.allow_local_fallback,
            optional=policy_stage.optional,
        )

    def _owner_str(self, candidate: ProviderCandidate) -> str:
        return f"{candidate.provider.name}/{candidate.model.alias}"

    def _balance_candidates(self, candidates: list[ProviderCandidate]) -> list[ProviderCandidate]:
        """Spread load across the fleet instead of always picking one node.

        Candidates are grouped by model alias (so requests for a given model rotate
        across the nodes that actually host it), and within each group the
        least-recently-used / least-busy node is preferred. Groups are ordered by
        their best score, so quality still wins overall, but equal-capability nodes
        share work -- idle machines get pulled in rather than one node being
        hammered while peers sit idle. This is what makes "so many nodes ready to
        go" actually get used."""
        if not candidates:
            return candidates
        groups: dict[str, list[ProviderCandidate]] = {}
        for c in candidates:
            groups.setdefault(c.model.alias, []).append(c)
        ordered_groups = sorted(groups.values(), key=lambda g: -max(c.score for c in g))
        balanced: list[ProviderCandidate] = []
        for g in ordered_groups:
            g.sort(
                key=lambda c: (
                    self._lb_inflight.get(self._owner_str(c), 0),
                    self._lb_last_used.get(self._owner_str(c), 0.0),
                    -c.score,
                )
            )
            balanced.extend(g)
        return balanced

    def mark_inflight_start(self, owner: str) -> None:
        with self._lb_lock:
            self._lb_inflight[owner] = self._lb_inflight.get(owner, 0) + 1
            self._lb_last_used[owner] = time.time()

    def mark_inflight_end(self, owner: str) -> None:
        with self._lb_lock:
            self._lb_inflight[owner] = max(0, self._lb_inflight.get(owner, 0) - 1)

    def _request_requires_local_execution(self, request: RouterRequest) -> bool:
        """Return True when the fleet privacy wall forbids cloud/public routes.

        Personal, voice/Sophia, explicit private, internal, sensitive, or
        local-only markers stay on local providers. Public APIs should receive
        only public or already-redacted payloads.
        """
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        model = request.model or ""
        if request.local_only or request.priority == Priority.local_only or request.allow_cloud is False:
            return True
        if model.startswith("auto/private") or model.startswith("auto/local") or model.startswith("auto/sophia"):
            return True
        privacy = str(metadata.get("privacy") or metadata.get("data_class") or "").strip().lower()
        if privacy in {"private", "personal", "internal", "secret", "sensitive", "local_only"}:
            return True
        if bool(metadata.get("sensitive")) or bool(metadata.get("private_data")):
            return True
        markers = metadata.get("markers") or metadata.get("tags") or []
        if isinstance(markers, str):
            markers = [markers]
        if isinstance(markers, list):
            normalized = {str(item).strip().lower() for item in markers if str(item).strip()}
            if normalized & {
                "private",
                "personal",
                "internal",
                "local_only",
                "private_data",
                "internal_docs",
                "personal_docs",
                "voice_auth",
                "enrollment_sample",
                "signal",
                "credentials",
                "secret",
            }:
                return True
        return False

    def _repo_hint(self, request: RouterRequest) -> str:
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        repo_path = str(metadata.get("repo_path") or request.metadata.get("repo_path") if isinstance(request.metadata, dict) else "")
        repo_name = str(metadata.get("repo_name") or metadata.get("repository") or "").strip().lower()
        text = f"{repo_path} {repo_name} {request.task_id or ''}"
        if "portfolio-management" in text.lower():
            return "portfolio-management"
        return ""

    def _provider_is_eligible(self, provider: ProviderConfig, request: RouterRequest) -> bool:
        canonical_provider = self.context.canonical_provider_name(provider.name)
        context_provider = self.context.provider_for(canonical_provider)
        lane = self._provider_lane(provider, context_provider)
        if context_provider and context_provider.is_blocked:
            return False
        if self._request_requires_local_execution(request):
            return lane == ExecutionLane.local
        return True

    def _model_matches(self, model: ModelConfig, required: set[str]) -> bool:
        return not required or required.issubset(model.capabilities)

    def _context_signals(self, provider: ProviderConfig, model: ModelConfig) -> list[ContextSignal]:
        signals: list[ContextSignal] = []
        canonical_provider = self.context.canonical_provider_name(provider.name)
        context_provider = self.context.provider_for(canonical_provider)
        signals.extend(self.context.signals_for_provider(canonical_provider))
        if context_provider is not None and context_provider.node_id:
            signals.extend(self.context.signals_for_node(context_provider.node_id))
        canonical_model = self.context.canonical_model_id(f"{provider.name}.{model.provider_model}")
        signals.extend(self.context.signals_for_model(canonical_model))
        if model.provider_model and model.provider_model != model.alias:
            signals.extend(self.context.signals_for_model(model.provider_model))
        merged: dict[str, ContextSignal] = {}
        for signal in signals:
            merged[signal.signal_id] = signal
        return list(merged.values())

    def _signal_score_adjustment(self, signals: list[ContextSignal], stage: PolicyStage) -> float:
        adjustment = 0.0
        for signal in signals:
            if not signal.is_active:
                continue
            weight = signal.strength if signal.strength else 1.0
            kind = signal.signal_type
            if signal.is_blocking:
                adjustment += 500.0
                continue
            if kind in {"preferred", "boost", "favour", "favor", "primary"}:
                adjustment -= 25.0 * weight
            elif kind in {"realtime", "low_latency", "latency_sensitive"}:
                adjustment -= 10.0 * weight
            elif kind in {"planning", "flash_planning"} and stage.purpose == StagePurpose.draft:
                adjustment -= 20.0 * weight
            elif kind in {"avoid", "deprioritized", "slow", "expensive"}:
                adjustment += 20.0 * weight
        return adjustment

    def _provider_signal_blocked(self, provider: ProviderConfig, model: ModelConfig) -> bool:
        return any(signal.is_blocking for signal in self._context_signals(provider, model))

    def _score(self, provider: ProviderConfig, model: ModelConfig, stage: PolicyStage, request: RouterRequest) -> float:
        score = float(provider.priority)
        canonical_provider = self.context.canonical_provider_name(provider.name)
        context_provider = self.context.provider_for(canonical_provider)
        lane = self._provider_lane(provider, context_provider)
        repo_hint = self._repo_hint(request)
        if lane == ExecutionLane.local:
            score -= 25
        elif lane == ExecutionLane.free_api:
            score -= 5
        if stage.purpose == StagePurpose.draft and str(provider.quota_class) == "local":
            score -= 50
        if stage.purpose in {StagePurpose.refine, StagePurpose.judge} and str(provider.quota_class) == "local":
            score += 100
        if "flash_planning" in model.capabilities and stage.purpose == StagePurpose.draft:
            score -= 45
        if "low_latency" in model.capabilities:
            score -= 5
        if "reasoning" in model.capabilities and stage.purpose in {StagePurpose.refine, StagePurpose.judge}:
            score -= 10
        if repo_hint == "portfolio-management":
            if context_provider and context_provider.node_id == "xwing":
                score -= 40
            elif context_provider and context_provider.node_id:
                score += 10
        if context_provider and context_provider.node_id:
            node = self.context.node_for(context_provider.node_id)
            if node and "gpu_accelerated" in node.capabilities:
                score -= 10
            if node is not None:
                score += self._node_preference_adjustment(node.capabilities, model.capabilities, stage)
        score += self._signal_score_adjustment(self._context_signals(provider, model), stage)
        if self._provider_signal_blocked(provider, model):
            score += 500
        return score

    def _node_preference_adjustment(self, node_capabilities: set[str], model_capabilities: set[str], stage: PolicyStage) -> float:
        adjustment = 0.0
        node_caps = {cap.strip().lower() for cap in node_capabilities}
        model_caps = {cap.strip().lower() for cap in model_capabilities}
        if stage.purpose in {StagePurpose.refine, StagePurpose.judge, StagePurpose.final}:
            if node_caps & {"long_context", "large_models"} and model_caps & {"reasoning", "large_context"}:
                adjustment -= 20.0
        if stage.purpose == StagePurpose.draft:
            if node_caps & {"fast_draft", "low_latency"} and model_caps & {"quick_iteration", "low_latency"}:
                adjustment -= 15.0
            if node_caps & {"gpu_accelerated", "fast_inference"} and model_caps & {"vram_fit", "low_latency", "moe"}:
                adjustment -= 12.0
        return adjustment

    def _provider_lane(self, provider: ProviderConfig, context_provider: ContextProvider | None) -> ExecutionLane:
        if context_provider is not None:
            return context_provider.lane
        if str(provider.quota_class) == "local" or provider.type == "lmstudio":
            return ExecutionLane.local
        return ExecutionLane.free_api

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
