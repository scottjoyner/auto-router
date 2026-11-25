"""Synthetic tests: outbox events project only the router's live signed
AssistX projection. Caller-supplied projection metadata is never evidence."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from auto_router.context import ContextSnapshot
from auto_router.event_outbox import EventOutbox
from auto_router.models import Priority, RouterRequest
from auto_router.providers import ProviderStreamResponse
from auto_router.route_events import (
    enqueue_route_decision_event,
    enqueue_route_execution_event,
    signed_projection_generation,
)
from auto_router.stream_lifecycle import install_stream_lifecycle

GENERATION = 42
REVISION = "rev-signed-42"
CHECKSUM = "ab" * 32


class Estimate:
    input_tokens = 10
    total_tokens = 20
    dimensions = {"rpm": 1, "tpd": 20}


def _manager(*, generation: int = GENERATION) -> SimpleNamespace:
    return SimpleNamespace(
        current=SimpleNamespace(
            generation=generation,
            revision=REVISION,
            checksum=CHECKSUM,
            expires_at_ms=9_999_999_999_999,
        )
    )


def _state(tmp_path, *, manager=None) -> SimpleNamespace:
    return SimpleNamespace(
        event_outbox=EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}"),
        context=ContextSnapshot(revision="rev-route", source="unit-test"),
        runtime_projection_manager=manager,
    )


def _request(**metadata) -> RouterRequest:
    return RouterRequest(
        request_id="req-signed-1",
        route="chat_completions",
        model="auto/flash-start",
        messages=[{"role": "user", "content": "synthetic prompt"}],
        priority=Priority.interactive,
        metadata=dict(metadata),
        raw_body={"messages": []},
    )


def _candidate():
    provider = SimpleNamespace(name="provider-1")
    model = SimpleNamespace(alias="local/model", provider_model="model.gguf")
    return SimpleNamespace(provider=provider, model=model)


def test_signed_projection_generation_matches_live_projection(tmp_path) -> None:
    state = _state(tmp_path, manager=_manager())
    metadata = {"runtime_projection_generation": GENERATION}
    assert signed_projection_generation(state, metadata) == GENERATION


def test_signed_projection_generation_rejects_forged_claims(tmp_path) -> None:
    state = _state(tmp_path, manager=_manager())
    assert signed_projection_generation(state, {"runtime_projection_generation": 999999}) is None
    # Strict integer typing: strings never qualify as signed projection state.
    assert signed_projection_generation(state, {"runtime_projection_generation": "42"}) is None
    assert signed_projection_generation(state, {"runtime_projection_generation": True}) is None
    assert signed_projection_generation(state, {}) is None


@pytest.mark.parametrize("invalid_generation", [True, False, "42", 42.0, 0, -1, None, {}])
def test_signed_projection_rejects_noninteger_manager_generation(
    tmp_path, invalid_generation
) -> None:
    state = _state(tmp_path, manager=_manager(generation=invalid_generation))
    # Even a valid caller request must not confer provenance on malformed
    # projection state, regardless of its apparent numeric value.
    assert signed_projection_generation(
        state, {"runtime_projection_generation": GENERATION}
    ) is None


def test_signed_projection_generation_requires_live_projection(tmp_path) -> None:
    state = _state(tmp_path, manager=None)
    metadata = {"runtime_projection_generation": GENERATION}
    assert signed_projection_generation(state, metadata) is None


def test_execution_event_projects_manager_values_not_caller_claims(tmp_path) -> None:
    state = _state(tmp_path, manager=_manager())
    request = _request(
        runtime_projection_generation=GENERATION,
        runtime_projection_revision="rev-forged-by-caller",
        runtime_projection_checksum="00" * 32,
    )
    enqueue_route_execution_event(
        state,
        request=request,
        provider="local-runtime",
        model="test-model",
        stage="draft",
        estimate=Estimate(),
        status_code=200,
        latency_ms=25,
    )
    payload = state.event_outbox.pending()[0]["payload"]
    assert payload["runtime_projection_generation"] == GENERATION
    assert payload["runtime_projection_revision"] == REVISION
    assert payload["runtime_projection_checksum"] == CHECKSUM


def test_execution_event_drops_forged_projection_claims(tmp_path) -> None:
    state = _state(tmp_path, manager=_manager())
    request = _request(
        runtime_projection_generation=999999,
        runtime_projection_revision="rev-forged-by-caller",
        runtime_projection_checksum="00" * 32,
    )
    enqueue_route_execution_event(
        state,
        request=request,
        provider="local-runtime",
        model="test-model",
        stage="draft",
        estimate=Estimate(),
        status_code=200,
        latency_ms=25,
    )
    payload = state.event_outbox.pending()[0]["payload"]
    assert payload["runtime_projection_generation"] is None
    assert payload["runtime_projection_revision"] is None
    assert payload["runtime_projection_checksum"] is None
    assert "rev-forged-by-caller" not in str(payload)


def test_decision_event_drops_forged_projection_claims(tmp_path) -> None:
    state = _state(tmp_path, manager=_manager())
    request = _request(runtime_projection_generation=999999)
    enqueue_route_decision_event(
        state,
        request=request,
        profile_name="balanced",
        stage="draft",
        chosen_candidate=_candidate(),
        candidates=[_candidate()],
    )
    event = state.event_outbox.pending()[0]
    payload = event["payload"]
    assert payload["runtime_projection_generation"] is None
    assert payload["runtime_projection_revision"] is None
    assert payload["runtime_projection_checksum"] is None


def test_decision_event_projects_live_signed_projection(tmp_path) -> None:
    state = _state(tmp_path, manager=_manager())
    request = _request(runtime_projection_generation=GENERATION)
    enqueue_route_decision_event(
        state,
        request=request,
        profile_name="balanced",
        stage="draft",
        chosen_candidate=_candidate(),
        candidates=[_candidate()],
    )
    payload = state.event_outbox.pending()[0]["payload"]
    assert payload["runtime_projection_generation"] == GENERATION
    assert payload["runtime_projection_revision"] == REVISION
    assert payload["runtime_projection_checksum"] == CHECKSUM


class _Policy:
    def __init__(self) -> None:
        self.active: dict[str, int] = {}

    def mark_inflight_start(self, owner: str) -> None:
        self.active[owner] = self.active.get(owner, 0) + 1

    def mark_inflight_end(self, owner: str) -> None:
        self.active[owner] = max(0, self.active.get(owner, 0) - 1)

    def mark_latency(self, owner: str, latency_ms: int) -> None:
        del latency_ms


class _Circuits:
    def record_success(self, owner: str) -> None:
        del owner

    def record_failure(self, owner: str, error: str, **kwargs) -> None:
        del owner, error, kwargs


class _Quota:
    def __init__(self) -> None:
        self.estimate_value = Estimate()

    def estimate(self, *args):
        return self.estimate_value

    def release(self, *args) -> None:
        del args


class _Idempotency:
    def transition(self, key: str, state: str, **kwargs) -> None:
        del key, state, kwargs


class _Outbox:
    def __init__(self) -> None:
        self.events: list = []

    def enqueue(self, event) -> str:
        self.events.append(event)
        return "event-1"


def _stream_module(body_text: bytes, *, manager):
    outbox = _Outbox()

    async def original_dispatch_stream(_provider, _candidate, _request, route_plan=None):
        del route_plan

        async def body():
            yield body_text

        return ProviderStreamResponse(
            provider="provider-1",
            model="model.gguf",
            status_code=200,
            headers={"content-type": "text/event-stream"},
            body=body(),
        )

    async def original_dispatch(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(status_code=200)

    def original_record_usage(*args, **kwargs) -> None:
        del args, kwargs

    state = SimpleNamespace(
        policy_engine=_Policy(),
        circuits=_Circuits(),
        quota=_Quota(),
        request_idempotency=_Idempotency(),
        event_outbox=outbox,
        runtime_projection_manager=manager,
    )
    module = SimpleNamespace(
        state=state,
        _dispatch=original_dispatch,
        _dispatch_stream=original_dispatch_stream,
        _record_usage=original_record_usage,
        _owner=lambda candidate: f"{candidate.provider.name}/{candidate.model.alias}",
    )
    install_stream_lifecycle(module)
    return module, outbox


def _stream_request(**metadata):
    return SimpleNamespace(
        request_id="req-stream-1",
        route="chat_completions",
        stream=True,
        task_id="task-1",
        metadata={"idempotency_key": "synthetic-key", **metadata},
        raw_body={"model": "auto/local", "messages": []},
    )


@pytest.mark.asyncio
async def test_stream_event_projects_live_signed_generation() -> None:
    module, outbox = _stream_module(b"data: [DONE]\n\n", manager=_manager())
    request = _stream_request(runtime_projection_generation=GENERATION)
    response = await module._dispatch_stream(None, _candidate(), request)
    _ = [chunk async for chunk in response.body]
    assert outbox.events[-1].event_type == "router.stream.completed"
    assert outbox.events[-1].payload["runtime_projection_generation"] == GENERATION


@pytest.mark.asyncio
async def test_stream_event_rejects_forged_projection_generation() -> None:
    module, outbox = _stream_module(b"data: [DONE]\n\n", manager=_manager())
    request = _stream_request(runtime_projection_generation=999999)
    response = await module._dispatch_stream(None, _candidate(), request)
    _ = [chunk async for chunk in response.body]
    assert outbox.events[-1].payload["runtime_projection_generation"] is None


@pytest.mark.asyncio
async def test_stream_event_reports_none_without_live_projection() -> None:
    module, outbox = _stream_module(b"data: [DONE]\n\n", manager=None)
    request = _stream_request(runtime_projection_generation=GENERATION)
    response = await module._dispatch_stream(None, _candidate(), request)
    _ = [chunk async for chunk in response.body]
    assert outbox.events[-1].payload["runtime_projection_generation"] is None
