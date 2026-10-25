import json

from auto_router.cache_affinity_route_event import emit_route_cache_affinity_shadow


def _identity(runtime_id: str):
    return {
        "model_hash": "sha-model-a",
        "quant": "Q4_K_M",
        "context_size": 32768,
        "runtime_id": runtime_id,
        "session_id": "session-1",
        "stable_prefix": "system+tools",
    }


def test_route_event_correlates_authoritative_node_and_affinity_choice(tmp_path):
    metadata = {
        "trace_id": "trace-1",
        "cache_affinity_shadow": {
            "request": _identity("node-b"),
            "candidates": [
                {
                    "candidate_id": "node-a",
                    "eligible": True,
                    "cache_identity": _identity("node-a"),
                },
                {
                    "candidate_id": "node-b",
                    "eligible": True,
                    "cache_identity": _identity("node-b"),
                },
            ],
        },
    }
    decision = {
        "correlation_id": "request-1",
        "route_id": "route-1",
        "target_node_id": "node-a",
        "target_service": "lmstudio:model",
    }
    target = tmp_path / "shadow.jsonl"
    event = emit_route_cache_affinity_shadow(
        metadata=metadata,
        decision=decision,
        path=target,
    )
    assert event is not None
    assert event["authoritative_behavior_changed"] is False
    assert event["actual_candidate_id"] == "node-a"
    assert event["affinity_candidate_id"] == "node-b"
    assert event["agreed"] is False
    assert event["trace_id"] == "trace-1"
    assert json.loads(target.read_text())["request_id"] == "request-1"


def test_missing_cache_telemetry_does_not_emit(tmp_path):
    target = tmp_path / "shadow.jsonl"
    event = emit_route_cache_affinity_shadow(
        metadata={},
        decision={"correlation_id": "request-1", "target_node_id": "node-a"},
        path=target,
    )
    assert event is None
    assert not target.exists()


def test_emitter_never_mutates_authoritative_decision(tmp_path):
    decision = {
        "correlation_id": "request-1",
        "target_node_id": "node-a",
        "status": "selected",
    }
    original = dict(decision)
    emit_route_cache_affinity_shadow(
        metadata={
            "cache_affinity_shadow": {
                "request": _identity("node-a"),
                "candidates": [
                    {
                        "candidate_id": "node-a",
                        "eligible": True,
                        "cache_identity": _identity("node-a"),
                    }
                ],
            }
        },
        decision=decision,
        path=tmp_path / "shadow.jsonl",
    )
    assert decision == original
