from types import SimpleNamespace

from auto_router.context import ContextSnapshot
from auto_router.event_outbox import EventOutbox
from auto_router.models import Priority, RouterRequest
from auto_router.route_events import enqueue_route_execution_event


class Estimate:
    input_tokens = 10
    total_tokens = 20
    dimensions = {"rpm": 1, "tpd": 20}


def test_enqueue_route_execution_event_excludes_prompt_body(tmp_path) -> None:
    state = SimpleNamespace(
        event_outbox=EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}"),
        context=ContextSnapshot(revision="rev-route", source="unit-test"),
    )
    request = RouterRequest(
        request_id="req-1",
        route="chat_completions",
        model="auto/flash-start",
        messages=[{"role": "user", "content": "secret prompt text"}],
        priority=Priority.interactive,
        raw_body={"messages": [{"role": "user", "content": "secret prompt text"}]},
    )

    enqueue_route_execution_event(
        state,
        request=request,
        provider="cerebras",
        model="gpt-oss-120b",
        stage="draft",
        estimate=Estimate(),
        status_code=200,
        latency_ms=50,
        usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    )

    event = state.event_outbox.pending()[0]
    payload = event["payload"]

    assert event["event_type"] == "router.execution_stage.completed"
    assert payload["request_id"] == "req-1"
    assert payload["provider"] == "cerebras"
    assert payload["model"] == "gpt-oss-120b"
    assert payload["input_tokens"] == 11
    assert "secret prompt text" not in str(payload)
    assert "raw_body" not in payload
    assert "messages" not in payload


def test_enqueue_route_execution_event_records_failure(tmp_path) -> None:
    state = SimpleNamespace(
        event_outbox=EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}"),
        context=ContextSnapshot(revision="rev-route", source="unit-test"),
    )
    request = RouterRequest(request_id="req-2", route="chat_completions", model="auto/fast")
    error = RuntimeError("provider failed")

    enqueue_route_execution_event(
        state,
        request=request,
        provider="groq",
        model="llama",
        stage="final",
        estimate=Estimate(),
        status_code=503,
        latency_ms=100,
        error=error,
    )

    event = state.event_outbox.pending()[0]
    payload = event["payload"]

    assert event["event_type"] == "router.execution_stage.failed"
    assert payload["status"] == "failed"
    assert payload["error_type"] == "RuntimeError"
    assert payload["error_message"] == "provider failed"
