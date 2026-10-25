import json

from auto_router.cache_affinity_shadow import CacheAffinityShadowResult
from auto_router.cache_affinity_shadow_event import (
    append_cache_affinity_shadow_event,
    cache_affinity_shadow_event,
)


def _result():
    return CacheAffinityShadowResult(
        actual_candidate_id="runtime-a",
        affinity_candidate_id="runtime-b",
        affinity_score=7,
        agreed=False,
        considered=2,
        excluded_ineligible=1,
    )


def test_shadow_event_is_explicitly_non_authoritative():
    event = cache_affinity_shadow_event(
        _result(),
        request_id="request-1",
        trace_id="trace-1",
        model_hash="model-a",
        runtime_id="runtime-a",
    )
    assert event["schema_version"] == "auto-router.cache-affinity-shadow.v1"
    assert event["authoritative_behavior_changed"] is False
    assert event["actual_candidate_id"] == "runtime-a"
    assert event["affinity_candidate_id"] == "runtime-b"
    assert event["trace_id"] == "trace-1"


def test_shadow_event_sink_is_opt_in_and_jsonl(tmp_path):
    event = cache_affinity_shadow_event(_result(), request_id="request-1")
    assert append_cache_affinity_shadow_event(event, "") is None
    target = tmp_path / "shadow.jsonl"
    assert append_cache_affinity_shadow_event(event, target) == str(target)
    payload = json.loads(target.read_text().strip())
    assert payload["request_id"] == "request-1"
    assert payload["affinity_score"] == 7
