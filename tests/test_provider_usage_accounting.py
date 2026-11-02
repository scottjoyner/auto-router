"""Only upstream-provided token counts are usage; never quota estimates."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from auto_router.main import _build_runtime_sample
from auto_router.models import RouterRequest
from auto_router.usage_accounting import provider_reported_usage


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({}, (0, 0, 0, False)),
        (None, (0, 0, 0, False)),
        ({"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
         (12, 4, 16, True)),
        ({"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
         (8, 2, 10, True)),
        ({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
         (0, 0, 0, True)),
        ({"total_tokens": 20}, (0, 0, 20, True)),
        ({"prompt_tokens": 10}, (10, 0, 10, True)),
        ({"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 12},
         (0, 0, 0, False)),
        ({"prompt_tokens": 8, "input_tokens": 7}, (0, 0, 0, False)),
        ({"prompt_tokens": -1}, (0, 0, 0, False)),
        ({"prompt_tokens": True}, (0, 0, 0, False)),
        ({"prompt_tokens": "100"}, (0, 0, 0, False)),
    ],
)
def test_provenance_parser(usage, expected):
    assert provider_reported_usage(usage) == expected


@pytest.mark.parametrize(
    ("status", "usage", "error", "expected_counts", "expected_rate"),
    [
        (None, {}, RuntimeError("signed AssistX runtime projection is not configured"),
         (0, 0, 0), None),
        (503, {}, RuntimeError("provider unavailable"), (0, 0, 0), None),
        (200, {}, None, (0, 0, 0), None),
        (200, {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17},
         None, (10, 7, 17), 7.0),
        (503, {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
         RuntimeError("incomplete request"), (10, 2, 12), None),
        (200, {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
         None, (3, 4, 7), 4.0),
        (200, {"prompt_tokens": 3, "completion_tokens": 0}, None,
         (3, 0, 3), None),
    ],
)
def test_runtime_sample_never_promotes_quota_estimate(
    status, usage, error, expected_counts, expected_rate
):
    request = RouterRequest(request_id="req", route="chat_completions", model="auto/test")
    estimate = SimpleNamespace(
        input_tokens=700, total_tokens=900, dimensions={"estimated_tpm": 900}
    )
    sample = _build_runtime_sample(
        request, "local", "model", "final", estimate,
        status, 1000, usage, error, None, 1_000, 2_000
    )
    assert (sample.input_tokens, sample.output_tokens, sample.total_tokens) == expected_counts
    assert sample.tokens_per_second == expected_rate
    assert sample.value_per_second == expected_rate


def test_embedding_prompt_only_never_becomes_decode_throughput():
    request = RouterRequest(request_id="embed", route="embeddings", model="embed")
    sample = _build_runtime_sample(
        request, "local", "embed", "final",
        SimpleNamespace(input_tokens=500, total_tokens=512), 200, 400,
        {"prompt_tokens": 16, "total_tokens": 16}, None, {}, 1000, 1400,
    )
    assert sample.total_tokens == 16
    assert sample.output_tokens == 0
    assert sample.tokens_per_second is None
    assert sample.value_units == 0


def test_route_event_keeps_quota_estimate_separate_from_failed_usage(tmp_path):
    from auto_router.context import ContextSnapshot
    from auto_router.event_outbox import EventOutbox
    from auto_router.route_events import enqueue_route_execution_event

    outbox = EventOutbox(f"sqlite:///{tmp_path / 'outbox.sqlite3'}")
    state = SimpleNamespace(outbox=outbox, event_outbox=outbox,
                            context=ContextSnapshot(revision="test", source="synthetic"))
    request = RouterRequest(request_id="failed", route="embeddings", model="model")
    enqueue_route_execution_event(
        state, request=request, provider="local", model="model", stage="final",
        estimate=SimpleNamespace(input_tokens=25, total_tokens=537,
                                 dimensions={"tokens": 537}),
        status_code=None, latency_ms=150,
        usage=None, error=RuntimeError("signed AssistX runtime projection is not configured"),
    )
    payload = outbox.pending()[0]["payload"]
    assert payload["token_usage_source"] == "unknown"
    assert payload["input_tokens"] == 0
    assert payload["output_tokens"] == 0
    assert payload["total_tokens"] == 0
    assert payload["quota_units"] == {"tokens": 537}
    assert payload["tokens_per_second"] is None


def test_route_event_tags_provider_reported_usage(tmp_path):
    from auto_router.context import ContextSnapshot
    from auto_router.event_outbox import EventOutbox
    from auto_router.route_events import enqueue_route_execution_event

    outbox = EventOutbox(f"sqlite:///{tmp_path / 'outbox.sqlite3'}")
    state = SimpleNamespace(event_outbox=outbox,
                            context=ContextSnapshot(revision="test", source="synthetic"))
    request = RouterRequest(request_id="success", route="chat_completions", model="model")
    enqueue_route_execution_event(
        state, request=request, provider="local", model="model", stage="final",
        estimate=SimpleNamespace(input_tokens=999, total_tokens=9999,
                                 dimensions={"tokens": 9999}),
        status_code=200, latency_ms=200,
        usage={"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
    )
    payload = outbox.pending()[0]["payload"]
    assert payload["token_usage_source"] == "provider_reported"
    assert (payload["input_tokens"], payload["output_tokens"],
            payload["total_tokens"]) == (12, 7, 19)
