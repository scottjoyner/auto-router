from types import SimpleNamespace

from auto_router.context import ContextSnapshot
from auto_router.event_outbox import EventOutbox
from auto_router.models import Priority, RouterRequest
from auto_router.route_events import enqueue_route_decision_event, enqueue_route_execution_event


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


def test_route_event_records_gateway_metadata():
    state = SimpleNamespace(
        event_outbox=EventOutbox("sqlite:///:memory:"),
        context=SimpleNamespace(revision="ctx-1", source="assistx"),
    )
    request = SimpleNamespace(
        request_id="req-1",
        route="chat_completions",
        model="auto/code",
        priority=SimpleNamespace(value="repo_critical"),
        metadata={"profile": "high_priority_deliverable"},
        local_only=False,
        allow_cloud=True,
        stream=False,
    )

    enqueue_route_execution_event(
        state,
        request=request,
        provider="agentgateway-sidecar",
        model="gpt-4o",
        stage="judge",
        estimate=SimpleNamespace(input_tokens=1, total_tokens=2, dimensions={}),
        status_code=200,
        latency_ms=42,
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        gateway_metadata={"provider": "agentgateway-sidecar", "latency_ms": 42},
    )

    payload = state.event_outbox.pending()[0]["payload"]
    assert payload["gateway_used"] is True
    assert payload["gateway_provider"] == "agentgateway-sidecar"
    assert payload["gateway_latency_ms"] == 42


def test_route_decision_event_records_selection_and_rejections():
    state = SimpleNamespace(
        event_outbox=EventOutbox("sqlite:///:memory:"),
        context=SimpleNamespace(revision="ctx-2", source="assistx"),
    )
    request = RouterRequest(
        request_id="req-9",
        route="chat_completions",
        model="auto/fast",
        priority=Priority.interactive,
        metadata={"profile": "auto/fast"},
        local_only=False,
        allow_cloud=True,
    )
    candidate = SimpleNamespace(
        provider=SimpleNamespace(name="cerebras"),
        model=SimpleNamespace(alias="cerebras/flash", provider_model="gpt-oss-120b"),
        score=12.5,
        reason="matched draft",
    )
    enqueue_route_decision_event(
        state,
        request=request,
        profile_name="auto/fast",
        stage="draft",
        chosen_candidate=candidate,
        candidates=[candidate],
        rejections=["quota unavailable for groq/llama"],
    )

    payload = state.event_outbox.pending()[0]["payload"]
    assert payload["chosen"]["provider"] == "cerebras"
    assert payload["chosen"]["provider_model"] == "gpt-oss-120b"
    assert payload["rejections"] == ["quota unavailable for groq/llama"]
