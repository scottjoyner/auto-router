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
        task_id="assistx-task-123",
        agent_run_id="agent-run-456",
        node_id="deathstar-XPS-8920",
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
        started_at_ms=1_000,
        ended_at_ms=1_050,
        queue_wait_ms=12,
        load_time_ms=7,
        tokens_per_second=360.0,
        value_units=7,
        value_per_second=140.0,
    )

    event = state.event_outbox.pending()[0]
    payload = event["payload"]

    assert event["event_type"] == "router.execution_stage.completed"
    assert payload["request_id"] == "req-1"
    assert payload["provider"] == "cerebras"
    assert payload["model"] == "gpt-oss-120b"
    assert payload["task_id"] == "assistx-task-123"
    assert payload["agent_run_id"] == "agent-run-456"
    assert payload["node_id"] == "deathstar-XPS-8920"
    assert payload["input_tokens"] == 11
    assert payload["started_at_ms"] == 1_000
    assert payload["queue_wait_ms"] == 12
    assert payload["tokens_per_second"] == 360.0
    assert payload["value_per_second"] == 140.0
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


def test_route_execution_provider_model_id_does_not_double_prefix(tmp_path) -> None:
    state = SimpleNamespace(
        event_outbox=EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}"),
        context=ContextSnapshot(revision="rev-route", source="unit-test"),
    )
    request = RouterRequest(request_id="req-canonical", route="chat_completions", model="auto/private")

    enqueue_route_execution_event(
        state,
        request=request,
        provider="lmstudio-x1-370",
        model="lmstudio-x1-370.local/reasoning-large",
        stage="final",
        estimate=Estimate(),
        status_code=200,
        latency_ms=25,
    )

    payload = state.event_outbox.pending()[0]["payload"]
    assert payload["provider_id"] == "lmstudio-x1-370"
    assert payload["provider_model_id"] == "lmstudio-x1-370.local/reasoning-large"




def test_enqueue_route_execution_event_uses_provider_node_fallback(tmp_path) -> None:
    provider = SimpleNamespace(node_id="x1-370")
    state = SimpleNamespace(
        event_outbox=EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}"),
        context=SimpleNamespace(
            revision="rev-route",
            source="unit-test",
            canonical_provider_name=lambda value: str(value).strip().lower(),
            provider_for=lambda provider_name: provider if str(provider_name).strip().lower() == "lmstudio" else None,
        ),
        providers=SimpleNamespace(enabled=lambda: [SimpleNamespace(name="lmstudio", node_id="x1-370")]),
    )
    request = RouterRequest(request_id="req-node-fallback", route="chat_completions", model="auto/fast")

    enqueue_route_execution_event(
        state,
        request=request,
        provider="lmstudio",
        model="lmstudio.local/reasoning-large",
        stage="final",
        estimate=Estimate(),
        status_code=200,
        latency_ms=25,
    )

    payload = state.event_outbox.pending()[0]["payload"]
    assert payload["node_id"] == "x1-370"

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


def test_router_request_parses_assistx_task_metadata() -> None:
    from auto_router.main import _router_request

    request = _router_request(
        "chat_completions",
        {
            "model": "auto/backlog-burn",
            "metadata": {
                "task_id": "assistx-task-321",
                "agent_run_id": "agent-run-654",
                "node_id": "deathstar-XPS-8920",
                "profile": "backlog_burn",
            },
        },
    )

    assert request.task_id == "assistx-task-321"
    assert request.agent_run_id == "agent-run-654"
    assert request.node_id == "deathstar-XPS-8920"
    assert request.metadata["profile"] == "backlog_burn"


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
        metadata={"profile": "auto/fast", "task_id": "assistx-task-999", "assistx_source": True},
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
    assert payload["chosen"]["provider_id"] == "cerebras"
    assert payload["chosen"]["provider_model"] == "gpt-oss-120b"
    assert payload["chosen"]["model_id"] == "cerebras.gpt-oss-120b"
    assert payload["task_id"] == "assistx-task-999"
    assert payload["agent_run_id"] is None
    assert payload["node_id"] == "cerebras"
    assert payload["rejections"] == ["quota unavailable for groq/llama"]
