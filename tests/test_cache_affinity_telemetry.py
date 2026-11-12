import pytest

from auto_router.cache_affinity_route_shadow import observe_route_cache_affinity
from auto_router.cache_affinity_telemetry import (
    CacheAffinityTelemetryEnvelope,
    CandidateCacheTelemetry,
    RuntimeCacheIdentityTelemetry,
    envelope_from_mapping,
)


def ident(runtime_id: str, prefix: str = "system+tools"):
    return RuntimeCacheIdentityTelemetry(
        model_hash="sha-model-a",
        quant="Q4_K_M",
        context_size=32768,
        runtime_id=runtime_id,
        session_id="session-1",
        stable_prefix=prefix,
    )


def test_typed_envelope_renders_existing_shadow_bridge_shape():
    envelope = CacheAffinityTelemetryEnvelope(
        request=ident("node-b"),
        candidates=(
            CandidateCacheTelemetry("node-a", True, ident("node-a"), ttft_ms=320.0),
            CandidateCacheTelemetry("node-b", True, ident("node-b"), ttft_ms=180.0, cache_hit=True),
        ),
        trace_id="trace-1",
        correlation_id="request-1",
    )
    metadata = envelope.route_metadata()
    result = observe_route_cache_affinity(metadata, actual_candidate_id="node-a")
    assert result is not None
    assert result.affinity_candidate_id == "node-b"
    assert result.actual_candidate_id == "node-a"
    assert metadata["trace_id"] == "trace-1"
    assert len(envelope.fingerprint) == 64


def test_invalid_or_duplicate_runtime_telemetry_fails_closed():
    with pytest.raises(ValueError, match="candidate_id values must be unique"):
        CacheAffinityTelemetryEnvelope(
            request=ident("node-a"),
            candidates=(
                CandidateCacheTelemetry("node-a", True, ident("node-a")),
                CandidateCacheTelemetry("node-a", True, ident("node-a")),
            ),
        ).validate()

    with pytest.raises(ValueError, match="context_size"):
        RuntimeCacheIdentityTelemetry(
            model_hash="sha",
            quant="Q4",
            context_size=0,
            runtime_id="node-a",
            session_id="s",
            stable_prefix="p",
        ).validate()


def test_mapping_import_preserves_live_shaped_ttft_and_cache_hit_fields():
    envelope = envelope_from_mapping(
        {
            "trace_id": "trace-2",
            "correlation_id": "req-2",
            "request": {
                "model_hash": "sha-model-a",
                "quant": "Q4_K_M",
                "context_size": 32768,
                "runtime_id": "node-b",
                "session_id": "session-1",
                "stable_prefix": "system+tools",
            },
            "candidates": [
                {
                    "candidate_id": "node-b",
                    "eligible": True,
                    "cache_identity": {
                        "model_hash": "sha-model-a",
                        "quant": "Q4_K_M",
                        "context_size": 32768,
                        "runtime_id": "node-b",
                        "session_id": "session-1",
                        "stable_prefix": "system+tools",
                    },
                    "ttft_ms": 180,
                    "prefill_ms": 145,
                    "cache_hit": True,
                }
            ],
        }
    )
    candidate = envelope.candidates[0]
    assert candidate.ttft_ms == 180.0
    assert candidate.prefill_ms == 145.0
    assert candidate.cache_hit is True
