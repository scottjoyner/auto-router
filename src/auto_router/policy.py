from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from auto_router.config import PolicyRegistry, ProviderRegistry
from auto_router.settings import get_settings

_LATENCY_CACHE = Path(get_settings().latency_cache_path)

# Latency awareness for `auto`: nodes whose measured round-trip latency sits
# above the baseline are penalized so traffic flows to quick, idle nodes.
ROUTER_LATENCY_BASELINE_MS = 4000.0
ROUTER_LATENCY_MAX_PENALTY = 40.0
# Liveness awareness for `auto`: providers with poor recent health (from the
# router's probe history) are demoted so traffic avoids flaky/unloaded nodes.
ROUTER_HEALTH_PENALTY = 0.4
# Fresh, concrete evidence a provider has zero models loaded (or a very low
# recent health score) excludes it from routing entirely -- instead of waiting
# for a circuit breaker to trip after several slow failures.
ROUTER_LIVENESS_MAX_AGE_SECONDS = 60
ROUTER_LIVENESS_MIN_HEALTH = 25
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
        # Plan-time reservations: the top candidate of each stage is reserved when
        # plan() runs (atomically on the event loop) so that concurrent requests
        # planning in parallel don't all pick the same node. Released when the stage
        # starts executing (main._execute). Acts like a soft in-flight during planning.
        self._lb_planned: dict[str, int] = {}
        # Explicit round-robin counter so `auto` traffic rotates across the whole
        # fleet instead of stranding on a single node (latency/priority ties or
        # plan-batch races can otherwise park everything on one machine).
        self._rr_counter: int = 0
        # Latency awareness: exponential moving average of measured round-trip
        # latency per owner, so slow nodes are penalized and traffic flows to
        # quick, idle nodes instead of the single highest-priority one. Seeded
        # from disk (latency_ema.json) so the learning survives router restarts.
        self._latency_ema: dict[str, float] = self._load_latency_cache()
        self._latency_dirty: bool = False
        self._latency_last_write: float = 0.0
        # Liveness awareness: recent provider health (from the router's probe
        # history) keyed by provider name, used to demote/exclude flaky nodes.
        self.provider_health: dict[str, dict] = {}
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
        # Reserve the intended (top) candidate of each stage at plan time so
        # concurrent requests planning in parallel don't all pick the same node --
        # plan() runs atomically on the event loop, so the next request's plan sees
        # this reservation and balances to a different node. Released when the stage
        # actually starts executing (see main._execute).
        for st in stages:
            if st.candidates:
                self.mark_planned_start(self._owner_str(st.candidates[0]))
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

        Candidates are grouped by **capability tier** (modality + size bucket), not
        by exact model alias. Because every node hosts a *different* set of model
        keys, exact-alias grouping could never balance -- the single highest-scoring
        small model would win every time and the rest of the swarm would sit idle.
        Grouping by tier puts all small chat models (across every node) into one
        pool that is balanced by in-flight count + last-used, so `auto` traffic
        rotates across the whole fleet. Tiers are tried cheapest-first (embed <
        vision < text:small < text:mid < text:large) so quick nodes are preferred
        and bigger models are only used if the small tier is exhausted or fails."""
        if not candidates:
            return candidates
        groups: dict[str, list[ProviderCandidate]] = {}
        for c in candidates:
            groups.setdefault(self._capability_tier(c.model), []).append(c)
        # Text tiers first so a generic (capability-less) request prefers chat
        # models; embed/vision are only tried when nothing text-capable matches
        # (i.e. when they are explicitly required), never for plain chat.
        tier_rank = {"text:S": 0, "text:M": 1, "text:L": 2, "vision": 3, "embed": 4}
        ordered_groups = sorted(
            groups.values(),
            key=lambda g: tier_rank.get(self._capability_tier(g[0].model), 5),
        )
        balanced: list[ProviderCandidate] = []
        with self._lb_lock:
            self._rr_counter += 1
            rr = self._rr_counter
        for gi, g in enumerate(ordered_groups):
            g.sort(
                key=lambda c: (
                    self._lb_inflight.get(self._owner_str(c), 0)
                    + self._lb_planned.get(self._owner_str(c), 0),
                    # Least-recently-used wins so `auto` spreads across the fleet
                    # instead of parking on the highest-priority node. Latency and
                    # priority are weak secondary tiebreakers.
                    self._lb_last_used.get(self._owner_str(c), 0.0),
                    self._latency_term(self._owner_str(c)),
                    -c.score,
                )
            )
            # Rotate the tier by the round-robin counter so each request starts at a
            # different node -- guarantees even fleet utilization instead of stranding
            # on one machine when inflight/planned tie (parallel plan batches).
            if len(g) > 1:
                rot = rr % len(g)
                g = g[rot:] + g[:rot]
            balanced.extend(g)
        return balanced

    def _capability_tier(self, model: ModelConfig) -> str:
        """Normalized capability bucket so models of similar size/modality on
        *different* nodes balance against each other: 'embed', 'vision',
        'text:S' (<3B), 'text:M' (3-14B), 'text:L' (>14B)."""
        caps = {c.lower() for c in model.capabilities}
        if {"embed", "embedding"} & caps:
            modality = "embed"
        elif {"vision", "image"} & caps:
            modality = "vision"
        else:
            modality = "text"
        txt = f"{model.alias} {model.provider_model}"
        m = re.search(r"(\d+(?:\.\d+)?)\s*[Bb]", txt)
        if m:
            b = float(m.group(1))
        else:
            m2 = re.search(r"(\d+(?:\.\d+)?)\s*[Mm]", txt)
            b = float(m2.group(1)) / 1000.0 if m2 else 7.0
        if b < 3:
            tier = "S"
        elif b <= 14:
            tier = "M"
        else:
            tier = "L"
        return f"{modality}:{tier}"

    def mark_inflight_start(self, owner: str) -> None:
        with self._lb_lock:
            self._lb_inflight[owner] = self._lb_inflight.get(owner, 0) + 1
            self._lb_last_used[owner] = time.time()

    def mark_inflight_end(self, owner: str) -> None:
        with self._lb_lock:
            self._lb_inflight[owner] = max(0, self._lb_inflight.get(owner, 0) - 1)

    def mark_planned_start(self, owner: str) -> None:
        with self._lb_lock:
            self._lb_planned[owner] = self._lb_planned.get(owner, 0) + 1

    def mark_planned_end(self, owner: str) -> None:
        with self._lb_lock:
            self._lb_planned[owner] = max(0, self._lb_planned.get(owner, 0) - 1)

    def mark_latency(self, owner: str, latency_ms: float) -> None:
        """Fold a measured round-trip latency into the per-owner EMA.

        Called after every completed (or failed) route so the router learns which
        nodes are snappy and which are slow without relying on the over-accumulating
        `preferred` signal boosts that used to strand traffic on laggy nodes."""
        with self._lb_lock:
            # Clamp so a transient event-loop-saturation spike (or a stalled slow
            # node) can't permanently brand a node as unusable -- the EMA recovers
            # once real measurements flow again.
            sample = min(float(latency_ms), 25000.0)
            prev = self._latency_ema.get(owner)
            if prev is None:
                self._latency_ema[owner] = sample
            else:
                self._latency_ema[owner] = 0.3 * sample + 0.7 * prev
            self._latency_dirty = True

    # ---- latency EMA persistence (survives router restarts) ----------------- #
    def _load_latency_cache(self) -> dict[str, float]:
        try:
            if _LATENCY_CACHE.exists():
                with _LATENCY_CACHE.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
        except Exception:
            pass
        return {}

    def snapshot_latency(self) -> dict[str, float]:
        with self._lb_lock:
            return dict(self._latency_ema)

    def persist_latency(self, force: bool = False) -> None:
        """Write the latency EMA to disk. Throttled to latency_persist_interval_seconds
        unless `force` is set. Safe to call from a background task."""
        now = time.time()
        with self._lb_lock:
            if not force and not self._latency_dirty:
                return
            if not force and (now - self._latency_last_write) < get_settings().latency_persist_interval_seconds:
                return
            snapshot = dict(self._latency_ema)
            self._latency_dirty = False
            self._latency_last_write = now
        try:
            _LATENCY_CACHE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _LATENCY_CACHE.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(snapshot, fh)
            tmp.replace(_LATENCY_CACHE)
        except Exception:
            # best-effort persistence; never block routing on a disk error
            pass

    def _latency_penalty(self, owner: str) -> float:
        ema = self._latency_ema.get(owner)
        if ema is None or ema <= ROUTER_LATENCY_BASELINE_MS:
            return 0.0
        over = (ema - ROUTER_LATENCY_BASELINE_MS) / ROUTER_LATENCY_BASELINE_MS
        return min(ROUTER_LATENCY_MAX_PENALTY, over * ROUTER_LATENCY_MAX_PENALTY)

    def _latency_term(self, owner: str) -> float:
        """Normalized latency used as a balance-sort term: each ~2s of learned
        round-trip latency adds 1.0 to a node's effective "cost", so a fast node
        (even if busy) ranks well above an idle but slow node. Unmeasured nodes
        get a moderate cost (between known-fast and known-slow) so known-fast nodes
        are preferred, but unknowns are still tried (and measured) before known-slow
        ones -- never sending cold-start traffic blindly to the slowest machine.

        Scaled by settings.latency_term_weight so operators can disable latency
        bias (weight=0) and force even spread across the whole fleet.
        """
        ema = self._latency_ema.get(owner)
        if ema is None:
            raw = 3.0
        else:
            raw = ema / 2000.0
        return raw * get_settings().latency_term_weight

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
        if self._provider_liveness_dead(canonical_provider):
            return False
        if self._request_requires_local_execution(request):
            return lane == ExecutionLane.local
        return True

    def _provider_liveness_dead(self, canonical_provider: str) -> bool:
        """Exclude a provider only on fresh, concrete evidence it's unusable.

        We deliberately avoid excluding on missing/stale data (so a cold router
        or a briefly-flaky probe never wipes the fleet); we only drop a provider
        when the probe history shows it is currently online-but-empty (models
        unloaded) within the last ROUTER_LIVENESS_MAX_AGE_SECONDS."""
        report = self.provider_health.get(canonical_provider)
        if not report:
            return False
        if report.get("stale"):
            return False
        if report.get("age_seconds", 9999) > ROUTER_LIVENESS_MAX_AGE_SECONDS:
            return False
        ok = report.get("ok")
        model_count = report.get("model_count", 0) or 0
        # Online endpoint that reports zero loaded models -> don't route to it.
        if ok is False and model_count == 0:
            return True
        # Consistently-failing provider (very low recent health score) with fresh
        # probe data -> exclude so we don't burn an attempt on a known-bad node.
        health_score = report.get("health_score")
        if health_score is not None and health_score < ROUTER_LIVENESS_MIN_HEALTH:
            return True
        return False

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
        # Latency awareness: penalize owners whose measured latency sits above the
        # baseline so `auto` prefers quick nodes over laggy ones.
        owner = f"{provider.name}/{model.alias}"
        score += self._latency_penalty(owner)
        # Liveness awareness: demote providers with poor recent health so traffic
        # avoids flaky / frequently-failing nodes even before circuits trip.
        score += self._health_penalty(provider.name)
        return score

    def _health_penalty(self, provider_name: str) -> float:
        report = self.provider_health.get(provider_name)
        if not report:
            return 0.0
        score = report.get("health_score")
        if score is None:
            return 0.0
        return max(0.0, (100 - score)) * ROUTER_HEALTH_PENALTY

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
