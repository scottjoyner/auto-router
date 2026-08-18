from auto_router.cache_affinity_telemetry import (
    CacheAffinityTelemetryEnvelope,
    CandidateCacheTelemetry,
    RuntimeCacheIdentityTelemetry,
)
from auto_router.cache_affinity_ttft_report import (
    evaluate_ttft_observation,
    summarize_ttft_evidence,
)


def ident(runtime_id: str):
    return RuntimeCacheIdentityTelemetry(
        model_hash="sha-model-a",
        quant="Q4_K_M",
        context_size=32768,
        runtime_id=runtime_id,
        session_id="session-1",
        stable_prefix="system+tools",
    )


def envelope(actual_ttft: float, affinity_ttft: float):
    return CacheAffinityTelemetryEnvelope(
        request=ident("node-b"),
        candidates=(
            CandidateCacheTelemetry("node-a", True, ident("node-a"), ttft_ms=actual_ttft),
            CandidateCacheTelemetry("node-b", True, ident("node-b"), ttft_ms=affinity_ttft, cache_hit=True),
        ),
        trace_id="trace-1",
    )


def test_ttft_observation_compares_authoritative_and_affinity_candidates():
    observation = evaluate_ttft_observation(envelope(300, 180), actual_candidate_id="node-a")
    assert observation.affinity_candidate_id == "node-b"
    assert observation.actual_ttft_ms == 300
    assert observation.affinity_ttft_ms == 180
    assert observation.ttft_reduction_ratio == 0.4
    assert observation.agreed is False


def test_summary_emits_promotion_relevant_median_and_missing_counts():
    observations = [
        evaluate_ttft_observation(envelope(300, 180), actual_candidate_id="node-a"),
        evaluate_ttft_observation(envelope(400, 320), actual_candidate_id="node-a"),
    ]
    summary = summarize_ttft_evidence(observations)
    assert summary["observations"] == 2
    assert summary["comparable_ttft_observations"] == 2
    assert summary["median_ttft_reduction_ratio"] == 0.3
    assert summary["routing_safety_regressions"] == 0
    assert summary["authoritative_behavior_changed"] is False
