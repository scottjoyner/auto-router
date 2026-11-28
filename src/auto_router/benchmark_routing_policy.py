from __future__ import annotations

import math
from typing import Any

from .models import ExecutionStage, RouterRequest

_FAMILY_ALIASES = {
    "code": "coding",
    "code_review": "coding",
    "research": "reasoning",
    "analysis": "reasoning",
    "tools": "tool_use",
    "tool": "tool_use",
    "summary": "summarization",
    "summarize": "summarization",
    "compress": "compression",
    "extract": "extraction",
    "context": "long_context",
}

_FAMILY_ROLES: dict[str, set[str]] = {
    "coding": {"full_agent", "code_agent"},
    "reasoning": {"full_agent", "reasoning"},
    "tool_use": {"full_agent", "tool_agent"},
    "long_context": {"full_agent", "long_context"},
    "summarization": {"full_agent", "auxiliary_llm", "summarization"},
    "compression": {"full_agent", "auxiliary_llm", "compression"},
    "extraction": {"full_agent", "auxiliary_llm", "extraction"},
}

_PROFILE_BY_FAMILY = {
    "summarization": "summarization_local",
    "compression": "compression_local",
    "extraction": "extraction_local",
}

_INSTALLED = False


def normalize_task_family(request: RouterRequest) -> str:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    raw = str(
        metadata.get("task_family")
        or metadata.get("workload_class")
        or metadata.get("task_kind")
        or ""
    ).strip().lower().replace("-", "_").replace(" ", "_")
    if raw:
        return _FAMILY_ALIASES.get(raw, raw)
    model = str(request.model or "").lower()
    if model.startswith(("auto/summarize", "auto/summary")):
        return "summarization"
    if model.startswith(("auto/compress", "auto/compact")):
        return "compression"
    if model.startswith(("auto/extract", "auto/parse")):
        return "extraction"
    if model.startswith("auto/code") or request.priority.value == "repo_critical":
        return "coding"
    if request.tools:
        return "tool_use"
    return "general"


def _observer_only(candidate: Any) -> bool:
    """Explicit signed observer-only policy can never become routing authority.

    Legacy providers without routing metadata retain their historical behavior;
    a signed missing-policy denial is represented by worker_mode=observer_only.
    """
    return any(
        str(mode or "").strip().lower() == "observer_only"
        for mode in (candidate.model.worker_mode, candidate.provider.worker_mode)
    )


def _role_allowed(candidate: Any, family: str) -> bool:
    model = candidate.model
    provider = candidate.provider
    if _observer_only(candidate):
        return False
    # A mismatch between provider/model permissions must never grant execution.
    if family == "coding" and not (
        model.allow_code_execution and provider.allow_code_execution
    ):
        return False
    # Model-level roles are authoritative: provider-level roles are a legacy
    # fallback only when the model declares none. Unioning them would let a
    # broad provider role widen a narrow model restriction.
    roles = set(model.routing_roles) or set(provider.routing_roles)
    if not roles:
        return True
    required = _FAMILY_ROLES.get(family)
    if required and not roles.intersection(required):
        return False
    return True


def _quality_allowed(candidate: Any, family: str) -> bool:
    evidence = candidate.model.task_family_scores.get(family)
    if not isinstance(evidence, dict):
        return True
    return evidence.get("quality_floor_passed") is True


def _evidence_float(evidence: dict[str, Any], key: str, default: float = 0.0) -> float | None:
    """Return a finite float for benchmark evidence, or ``None`` if invalid."""
    value = evidence.get(key)
    if value is None or isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _benchmark_key(
    candidate: Any,
    family: str,
    original_index: int,
) -> tuple[Any, ...]:
    evidence = candidate.model.task_family_scores.get(family)
    if not isinstance(evidence, dict):
        return (1, 0.0, 0.0, original_index)
    utility = _evidence_float(evidence, "utility_score")
    confidence = _evidence_float(evidence, "quality_confidence")
    quality = _evidence_float(evidence, "quality_score")
    if utility is None or confidence is None or quality is None:
        # Malformed evidence gets the unmeasured fallback instead of raising a
        # 500; never let invalid numbers outrank validated evidence.
        return (1, 0.0, 0.0, original_index)
    # Qualified evidence ranks before unmeasured eligible candidates. Original
    # order remains the final tiebreaker so live load balancing is preserved.
    return (0, -utility, -quality * max(confidence, 0.25), original_index)


def benchmark_order(
    stage: ExecutionStage,
    request: RouterRequest,
) -> ExecutionStage:
    family = normalize_task_family(request)
    if not stage.candidates:
        return stage
    if family == "general":
        # Do not let the legacy general-workload fast path bypass an explicit
        # signed observer-only denial. Preserve relative order otherwise.
        return stage.model_copy(
            update={
                "candidates": [
                    candidate for candidate in stage.candidates
                    if not _observer_only(candidate)
                ]
            }
        )
    allowed = [
        candidate
        for candidate in stage.candidates
        if _role_allowed(candidate, family)
        and _quality_allowed(candidate, family)
    ]
    indexed = list(enumerate(allowed))
    indexed.sort(key=lambda item: _benchmark_key(item[1], family, item[0]))
    candidates = [candidate for _, candidate in indexed]
    for candidate in candidates:
        evidence = candidate.model.task_family_scores.get(family)
        if isinstance(evidence, dict):
            utility = _evidence_float(evidence, "utility_score")
            quality = _evidence_float(evidence, "quality_score")
            if utility is None or quality is None:
                candidate.reason = (
                    f"{candidate.reason}; {family} benchmark evidence invalid; "
                    "validation fallback applied"
                )
            else:
                candidate.reason = (
                    f"{candidate.reason}; {family} benchmark utility={utility:.3f}, "
                    f"quality={quality:.3f}, tps={evidence.get('tokens_per_second')}"
                )
        else:
            candidate.reason = (
                f"{candidate.reason}; no {family} benchmark evidence"
            )
    return stage.model_copy(update={"candidates": candidates})


def install_benchmark_routing_policy() -> None:
    """Install benchmark ordering without making benchmark data an authority."""

    global _INSTALLED
    if _INSTALLED:
        return
    from .policy import PolicyEngine

    original_classify = PolicyEngine.classify_profile
    original_build_stage = PolicyEngine._build_stage

    def classify_profile(self: Any, request: RouterRequest) -> str:
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        explicit = metadata.get("profile")
        if isinstance(explicit, str) and explicit in self.policies.profiles:
            # Original classification already handles explicit metadata profiles
            # and any safety overrides that take precedence over them. Benchmark
            # inference must not supersede an operator-selected profile.
            return original_classify(self, request)
        family = normalize_task_family(request)
        profile = _PROFILE_BY_FAMILY.get(family)
        if profile and profile in self.policies.profiles:
            return profile
        return original_classify(self, request)

    def build_stage(
        self: Any,
        policy_stage: Any,
        request: RouterRequest,
    ) -> ExecutionStage:
        stage = original_build_stage(self, policy_stage, request)
        return benchmark_order(stage, request)

    PolicyEngine.classify_profile = classify_profile
    PolicyEngine._build_stage = build_stage
    _INSTALLED = True
