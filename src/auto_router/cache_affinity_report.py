"""Aggregate cache-affinity shadow observations across requests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Iterable

from .cache_affinity_shadow import CacheAffinityShadowResult


@dataclass(frozen=True)
class CacheAffinityShadowReport:
    observations: int
    agreement_rate: float
    disagreement_count: int
    mean_affinity_score: float
    total_excluded_ineligible: int
    no_candidate_count: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_cache_affinity_shadow(
    observations: Iterable[CacheAffinityShadowResult],
) -> CacheAffinityShadowReport:
    rows = tuple(observations)
    return CacheAffinityShadowReport(
        observations=len(rows),
        agreement_rate=(sum(row.agreed for row in rows) / len(rows)) if rows else 0.0,
        disagreement_count=sum(not row.agreed for row in rows),
        mean_affinity_score=mean(row.affinity_score for row in rows) if rows else 0.0,
        total_excluded_ineligible=sum(row.excluded_ineligible for row in rows),
        no_candidate_count=sum(row.affinity_candidate_id is None for row in rows),
    )
