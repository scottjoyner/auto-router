"""Shadow-mode cache-affinity telemetry.

This module compares the authoritative route with the cache-affinity preference
without changing execution. It is safe to emit as experiment evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .cache_affinity import CacheIdentity
from .cache_affinity_policy import AffinityCandidate, rank_eligible_affinity


@dataclass(frozen=True)
class CacheAffinityShadowResult:
    actual_candidate_id: str | None
    affinity_candidate_id: str | None
    affinity_score: int
    agreed: bool
    considered: int
    excluded_ineligible: int

    def as_metadata(self) -> dict[str, object]:
        return {"shadow_cache_affinity": asdict(self)}


def observe_cache_affinity(
    request: CacheIdentity,
    candidates: list[AffinityCandidate],
    *,
    actual_candidate_id: str | None,
) -> CacheAffinityShadowResult:
    """Return what affinity would prefer while preserving the actual route."""
    decision = rank_eligible_affinity(request, candidates)
    return CacheAffinityShadowResult(
        actual_candidate_id=actual_candidate_id,
        affinity_candidate_id=decision.candidate_id,
        affinity_score=decision.score,
        agreed=actual_candidate_id == decision.candidate_id,
        considered=decision.considered,
        excluded_ineligible=decision.excluded_ineligible,
    )
