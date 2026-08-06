from __future__ import annotations

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


def _role_allowed(candidate: Any, family: str) -> bool:
    model = candidate.model
    provider = candidate.provider
    roles = set(model.routing_roles) | set(provider.routing_roles)
    if not roles:
        return True
    required = _FAMILY_ROLES.get(family)
    if required and not roles.intersection(required):
        return False
    if family == "coding" and not (
        model.allow_code_execution or provider.allow_code_execution
    ):
        return False
    return True


def _quality_allowed(candidate: Any, family: str) -> bool:
    evidence = candidate.model.task_family_scores.get(family)
    if not isinstance(evidence, dict):
        return True
    return evidence.get("quality_floor_passed") is True


def _benchmark_key(
    candidate: Any,
    family: str,
    original_index: int,
) -> tuple[Any, ...]:
    evidence = candidate.model.task_family_scores.get(family)
    if not isinstance(evidence, dict):
        return (1, 0.0, 0.0, original_index)
    utility = float(evidence.get("utility_score") or 0.0)
    confidence = float(evidence.get("quality_confidence") or 0.0)
    quality = float(evidence.get("quality_score") or 0.0)
    # Qualified evidence ranks before unmeasured eligible candidates. Original
    # order remains the final tiebreaker so live load balancing is preserved.
    return (0, -utility, -quality * max(confidence, 0.25), original_index)


def benchmark_order(
    stage: ExecutionStage,
    request: RouterRequest,
) -> ExecutionStage:
    family = normalize_task_family(request)
    if family == "general" or not stage.candidates:
        return stage
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
            candidate.reason = (
                f"{candidate.reason}; {family} benchmark utility="
                f"{float(evidence.get('utility_score') or 0.0):.3f}, "
                f"quality={float(evidence.get('quality_score') or 0.0):.3f}, "
                f"tps={evidence.get('tokens_per_second')}"
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
