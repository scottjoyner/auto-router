"""Side-channel cache-affinity ranking that cannot make a runtime eligible."""

from __future__ import annotations

from dataclasses import dataclass

from .cache_affinity import CacheIdentity, affinity_score


@dataclass(frozen=True)
class AffinityCandidate:
    candidate_id: str
    cache_identity: CacheIdentity
    eligible: bool


@dataclass(frozen=True)
class AffinityDecision:
    candidate_id: str | None
    score: int
    considered: int
    excluded_ineligible: int


def rank_eligible_affinity(
    request: CacheIdentity, candidates: list[AffinityCandidate]
) -> AffinityDecision:
    """Rank cache locality only among candidates already declared eligible upstream."""
    eligible = [candidate for candidate in candidates if candidate.eligible]
    excluded = len(candidates) - len(eligible)
    if not eligible:
        return AffinityDecision(None, 0, 0, excluded)

    scored = [
        (affinity_score(request, candidate.cache_identity), candidate.candidate_id)
        for candidate in eligible
    ]
    score, candidate_id = max(scored, key=lambda item: (item[0], item[1]))
    return AffinityDecision(candidate_id, score, len(eligible), excluded)
