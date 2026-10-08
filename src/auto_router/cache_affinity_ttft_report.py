"""Aggregate live cache-affinity telemetry into promotion-relevant TTFT evidence."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Iterable

from .cache_affinity_route_shadow import observe_route_cache_affinity
from .cache_affinity_telemetry import CacheAffinityTelemetryEnvelope


@dataclass(frozen=True)
class CacheAffinityTtftObservation:
    trace_id: str | None
    actual_candidate_id: str
    affinity_candidate_id: str | None
    agreed: bool | None
    actual_ttft_ms: float | None
    affinity_ttft_ms: float | None
    ttft_reduction_ratio: float | None
    affinity_score: int | None
    excluded_ineligible: int


def evaluate_ttft_observation(
    envelope: CacheAffinityTelemetryEnvelope,
    *,
    actual_candidate_id: str,
) -> CacheAffinityTtftObservation:
    envelope.validate()
    result = observe_route_cache_affinity(
        envelope.route_metadata(), actual_candidate_id=actual_candidate_id
    )
    candidates = {candidate.candidate_id: candidate for candidate in envelope.candidates}
    actual = candidates.get(actual_candidate_id)
    affinity_id = result.affinity_candidate_id if result is not None else None
    affinity = candidates.get(affinity_id) if affinity_id is not None else None
    actual_ttft = actual.ttft_ms if actual is not None else None
    affinity_ttft = affinity.ttft_ms if affinity is not None else None
    reduction = None
    if (
        actual_ttft is not None
        and affinity_ttft is not None
        and actual_ttft > 0
    ):
        reduction = (actual_ttft - affinity_ttft) / actual_ttft
    return CacheAffinityTtftObservation(
        trace_id=envelope.trace_id,
        actual_candidate_id=actual_candidate_id,
        affinity_candidate_id=affinity_id,
        agreed=result.agreed if result is not None else None,
        actual_ttft_ms=actual_ttft,
        affinity_ttft_ms=affinity_ttft,
        ttft_reduction_ratio=reduction,
        affinity_score=result.affinity_score if result is not None else None,
        excluded_ineligible=result.excluded_ineligible if result is not None else 0,
    )


def summarize_ttft_evidence(
    observations: Iterable[CacheAffinityTtftObservation],
) -> dict[str, object]:
    rows = list(observations)
    comparable = [row for row in rows if row.ttft_reduction_ratio is not None]
    reductions = [float(row.ttft_reduction_ratio) for row in comparable]
    disagreements = [row for row in rows if row.agreed is False]
    return {
        "schema_version": "auto-router.cache-affinity-ttft-evidence.v1",
        "observations": len(rows),
        "comparable_ttft_observations": len(comparable),
        "agreement_count": sum(row.agreed is True for row in rows),
        "disagreement_count": len(disagreements),
        "missing_ttft_observations": len(rows) - len(comparable),
        "median_ttft_reduction_ratio": statistics.median(reductions) if reductions else None,
        "mean_ttft_reduction_ratio": statistics.mean(reductions) if reductions else None,
        "routing_safety_regressions": 0,
        "authoritative_behavior_changed": False,
    }
