#!/usr/bin/env python3
"""Generate live-shaped cache-affinity telemetry and TTFT evidence without routing changes."""

from __future__ import annotations

import json
from pathlib import Path

from auto_router.cache_affinity_telemetry import (
    CacheAffinityTelemetryEnvelope,
    CandidateCacheTelemetry,
    RuntimeCacheIdentityTelemetry,
)
from auto_router.cache_affinity_ttft_report import (
    evaluate_ttft_observation,
    summarize_ttft_evidence,
)


def ident(runtime_id: str, *, prefix: str = "system+tools") -> RuntimeCacheIdentityTelemetry:
    return RuntimeCacheIdentityTelemetry(
        model_hash="sha-model-a",
        quant="Q4_K_M",
        context_size=32768,
        runtime_id=runtime_id,
        session_id="session-1",
        stable_prefix=prefix,
    )


def envelope(index: int, actual_ttft: float, affinity_ttft: float) -> CacheAffinityTelemetryEnvelope:
    return CacheAffinityTelemetryEnvelope(
        request=ident("node-b"),
        candidates=(
            CandidateCacheTelemetry(
                candidate_id="node-a",
                eligible=True,
                cache_identity=ident("node-a"),
                ttft_ms=actual_ttft,
                prefill_ms=max(0.0, actual_ttft - 30),
                cache_hit=False,
            ),
            CandidateCacheTelemetry(
                candidate_id="node-b",
                eligible=True,
                cache_identity=ident("node-b"),
                ttft_ms=affinity_ttft,
                prefill_ms=max(0.0, affinity_ttft - 30),
                cache_hit=True,
            ),
        ),
        trace_id=f"trace-{index}",
        correlation_id=f"request-{index}",
    )


def main() -> int:
    pairs = [(300, 180), (320, 190), (280, 175), (350, 210), (310, 200), (330, 205)]
    observations = [
        evaluate_ttft_observation(envelope(i, actual, affinity), actual_candidate_id="node-a")
        for i, (actual, affinity) in enumerate(pairs, start=1)
    ]
    payload = summarize_ttft_evidence(observations)
    payload["synthetic_live_shaped_fixture"] = True
    payload["promotion_evidence"] = False
    payload["observations_detail"] = [
        {
            "trace_id": row.trace_id,
            "actual_candidate_id": row.actual_candidate_id,
            "affinity_candidate_id": row.affinity_candidate_id,
            "actual_ttft_ms": row.actual_ttft_ms,
            "affinity_ttft_ms": row.affinity_ttft_ms,
            "ttft_reduction_ratio": row.ttft_reduction_ratio,
            "agreed": row.agreed,
        }
        for row in observations
    ]
    target = Path("cache-affinity-telemetry-smoke.json")
    target.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
