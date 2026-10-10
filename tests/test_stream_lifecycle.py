from __future__ import annotations

from types import SimpleNamespace

import pytest

from auto_router.providers import ProviderStreamResponse
from auto_router.stream_lifecycle import install_stream_lifecycle


class _Policy:
    def __init__(self) -> None:
        self.active: dict[str, int] = {}
        self.latencies: list[tuple[str, int]] = []

    def mark_inflight_start(self, owner: str) -> None:
        self.active[owner] = self.active.get(owner, 0) + 1

    def mark_inflight_end(self, owner: str) -> None:
        self.active[owner] = max(0, self.active.get(owner, 0) - 1)

    def mark_latency(self, owner: str, latency_ms: int) -> None:
        self.latencies.append((owner, latency_ms))


class _Circuits:
    def __init__(self) -> None:
        self.successes: list[str] = []
        self.failures: list[tuple[str, str]] = []

    def record_success(self, owner: str) -> None:
        self.successes.append(owner)

    def record_failure(self, owner: str, error: str, **_kwargs) -> None:
        self.failures.append((owner, error))


class _Quota:
    def __init__(self, estimate) -> None:
        self.estimate_value = estimate
        self.releases: list[tuple] = []

    def estimate(self, *_args):
        return self.estimate_value

    def release(self, *args):
        self.releases.append(args)


class _Idempotency:
    def __init__(self) -> None:
        self.transitions: list[tuple[str, str, dict]] = []

    def transition(self, key: str, state: str, **kwargs) -> None:
        self.transitions.append((key, state, kwargs))


class _Outbox:
    def __init__(self) -> None:
        self.events = []

    def enqueue(self, event) -> str:
        self.events.append(event)
        return event.event_id or "event-1"


def _request(*, stream: bool = True):
    return SimpleNamespace(
        request_id="request-1",
        route="chat_completions",
        stream=stream,
        task_id="task-1",
        metadata={
            "idempotency_key": "idempotency-hash",
            "assistx_executor": {"claim_id": "claim-1"},
            "runtime_projection_generation": 7,
            "runtime_instance_id": "runtime-1",
            "model_instance_id": "model-1",
        },
        raw_body={"model": "auto/local", "messages": []},
    )


def _candidate():
    provider = SimpleNamespace(name="provider-1")
    model = SimpleNamespace(alias="local/model", provider_model="model.gguf")
    return SimpleNamespace(provider=provider, model=model)


def _module(body_factory):
    estimate = SimpleNamespace(input_tokens=2, total_tokens=10, dimensions={"requests": 1})
    policy = _Policy()
    circuits = _Circuits()
    quota = _Quota(estimate)
    idempotency = _Idempotency()
    outbox = _Outbox()
    recorded: list[tuple] = []

    async def original_dispatch_stream(_provider, _candidate, _request, route_plan=None):
        del route_plan
        return ProviderStreamResponse(
            provider="provider-1",
            model="model.gguf",
            status_code=200,
            headers={"content-type": "text/event-stream"},
            body=body_factory(),
        )

    async def original_dispatch(*_args, **_kwargs):
        return SimpleNamespace(status_code=200)

    def original_record_usage(*args, **kwargs):
        recorded.append((args, kwargs))

    state = SimpleNamespace(
        policy_engine=policy,
        circuits=circuits,
        quota=quota,
        request_idempotency=idempotency,
        event_outbox=outbox,
    )
    module = SimpleNamespace(
        state=state,
        _dispatch=original_dispatch,
        _dispatch_stream=original_dispatch_stream,
        _record_usage=original_record_usage,
        _owner=lambda candidate: f"{candidate.provider.name}/{candidate.model.alias}",
    )
    install_stream_lifecycle(module)
    return module, estimate, recorded, policy, circuits, quota, idempotency, outbox


@pytest.mark.asyncio
async def test_stream_is_recorded_only_after_iterator_completion() -> None:
    async def body():
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        yield b'data: {"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5}}\n\n'
        yield b"data: [DONE]\n\n"

    (
        module,
        estimate,
        recorded,
        policy,
        circuits,
        _quota,
        idempotency,
        outbox,
    ) = _module(body)
    request = _request()
    candidate = _candidate()
    owner = module._owner(candidate)

    # Simulate the base execution loop's initial in-flight mark.
    policy.mark_inflight_start(owner)
    response = await module._dispatch_stream(None, candidate, request)
    # The base loop releases its original mark after response headers arrive.
    policy.mark_inflight_end(owner)
    module._record_usage(
        request,
        response.provider,
        response.model,
        "final",
        estimate,
        response.status_code,
        5,
        started_at_ms=1_000,
        ended_at_ms=1_005,
    )

    assert recorded == []
    assert policy.active[owner] == 1

    chunks = [chunk async for chunk in response.body]

    assert len(chunks) == 3
    assert policy.active[owner] == 0
    assert circuits.successes == [owner]
    assert len(recorded) == 1
    args, _kwargs = recorded[0]
    assert args[7] == {
        "prompt_tokens": 2,
        "completion_tokens": 3,
        "total_tokens": 5,
    }
    assert any(state == "completed" for _key, state, _data in idempotency.transitions)
    assert outbox.events[-1].event_type == "router.stream.completed"
    assert outbox.events[-1].payload["terminal_evidence"] == "openai_sse_done"
    assert outbox.events[-1].payload["usage_status"] == "reported"
    assert outbox.events[-1].payload["usage"]["total_tokens"] == 5


@pytest.mark.asyncio
async def test_stream_failure_releases_quota_and_remains_possibly_accepted() -> None:
    async def body():
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise RuntimeError("provider stream disconnected")

    (
        module,
        estimate,
        recorded,
        policy,
        circuits,
        quota,
        idempotency,
        outbox,
    ) = _module(body)
    request = _request()
    candidate = _candidate()
    owner = module._owner(candidate)

    policy.mark_inflight_start(owner)
    response = await module._dispatch_stream(None, candidate, request)
    policy.mark_inflight_end(owner)
    module._record_usage(
        request,
        response.provider,
        response.model,
        "final",
        estimate,
        response.status_code,
        5,
        started_at_ms=1_000,
        ended_at_ms=1_005,
    )

    with pytest.raises(RuntimeError, match="disconnected"):
        async for _chunk in response.body:
            pass

    assert policy.active[owner] == 0
    assert quota.releases
    assert circuits.failures
    assert len(recorded) == 1
    args, _kwargs = recorded[0]
    assert isinstance(args[8], RuntimeError)
    assert any(
        state == "possibly_accepted"
        for _key, state, _data in idempotency.transitions
    )
    assert outbox.events[-1].event_type == "router.stream.failed"
    assert outbox.events[-1].payload["usage_status"] == "pending"
    assert outbox.events[-1].payload["acceptance_state"] == "possibly_accepted"


def test_usage_collector_accepts_responses_api_usage_shape() -> None:
    from auto_router.stream_lifecycle import StreamUsageCollector

    collector = StreamUsageCollector()
    collector.feed(
        b'data: {"response":{"usage":{"input_tokens":4,"output_tokens":6,"total_tokens":10}}}\n\n'
    )
    collector.finish()

    assert collector.usage == {
        "prompt_tokens": 4,
        "completion_tokens": 6,
        "total_tokens": 10,
    }


@pytest.mark.asyncio
async def test_http_200_without_done_marker_is_incomplete_not_execution_success() -> None:
    async def body():
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        yield b'data: {"usage":{"prompt_tokens":3,"completion_tokens":1}}\n\n'

    module, estimate, recorded, policy, circuits, quota, idempotency, outbox = _module(body)
    req, candidate = _request(), _candidate()
    owner = module._owner(candidate)
    policy.mark_inflight_start(owner)
    response = await module._dispatch_stream(None, candidate, req)
    policy.mark_inflight_end(owner)
    module._record_usage(req, response.provider, response.model, "final", estimate, 200, 1)
    assert len([part async for part in response.body]) == 2
    assert policy.active[owner] == 0
    assert not circuits.successes
    assert quota.releases
    assert len(recorded) == 1
    assert outbox.events[-1].event_type == "router.stream.incomplete"
    assert outbox.events[-1].payload["terminal_evidence"] == "eof_without_terminal"
    assert outbox.events[-1].payload["acceptance_state"] == "possibly_accepted"
    assert outbox.events[-1].payload["usage_status"] == "reported"
    assert outbox.events[-1].payload["usage"]["prompt_tokens"] == 3
    assert not any(state == "completed" for _, state, _ in idempotency.transitions)


@pytest.mark.asyncio
async def test_responses_api_completed_marker_proves_clean_terminal_without_done() -> None:
    async def body():
        yield b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"ok"}\n\n'
        yield b'event: response.completed\ndata: {"type":"response.completed","response":{"usage":{"input_tokens":4,"output_tokens":2}}}\n\n'

    module, estimate, recorded, policy, circuits, quota, idempotency, outbox = _module(body)
    req, candidate = _request(), _candidate()
    policy.mark_inflight_start(module._owner(candidate))
    response = await module._dispatch_stream(None, candidate, req)
    policy.mark_inflight_end(module._owner(candidate))
    module._record_usage(req, response.provider, response.model, "final", estimate, 200, 1)
    assert len([part async for part in response.body]) == 2
    assert circuits.successes
    assert quota.releases == []
    assert len(recorded) == 1
    assert outbox.events[-1].event_type == "router.stream.completed"
    assert outbox.events[-1].payload["terminal_evidence"] == "responses_completed"
    assert outbox.events[-1].payload["usage"]["total_tokens"] == 6
    assert any(state == "completed" for _, state, _ in idempotency.transitions)


@pytest.mark.asyncio
async def test_responses_api_declared_failure_never_counts_as_success() -> None:
    async def body():
        yield b'event: response.failed\ndata: {"type":"response.failed"}\n\n'

    module, estimate, recorded, policy, circuits, quota, idempotency, outbox = _module(body)
    req, candidate = _request(), _candidate()
    owner = module._owner(candidate)
    policy.mark_inflight_start(owner)
    response = await module._dispatch_stream(None, candidate, req)
    policy.mark_inflight_end(owner)
    module._record_usage(req, response.provider, response.model, "final", estimate, 200, 1)
    assert len([part async for part in response.body]) == 1
    assert not circuits.successes
    assert quota.releases
    assert outbox.events[-1].event_type == "router.stream.incomplete"
    assert not any(status == "completed" for _, status, _ in idempotency.transitions)


@pytest.mark.asyncio
async def test_split_done_marker_across_network_chunks_is_valid_terminal() -> None:
    async def body():
        yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        yield b'data: [DO'
        yield b'NE]\n\n'

    module, estimate, recorded, policy, circuits, quota, idempotency, outbox = _module(body)
    req, candidate = _request(), _candidate()
    owner = module._owner(candidate)
    policy.mark_inflight_start(owner)
    response = await module._dispatch_stream(None, candidate, req)
    policy.mark_inflight_end(owner)
    module._record_usage(req, response.provider, response.model, "final", estimate, 200, 1)
    assert len([part async for part in response.body]) == 3
    assert circuits.successes
    assert outbox.events[-1].event_type == "router.stream.completed"
    assert quota.releases == []


def test_usage_parser_detects_terminal_without_retaining_prompt_text() -> None:
    from auto_router.stream_lifecycle import StreamUsageCollector

    collector = StreamUsageCollector()
    collector.feed(b'data: {"choices":[{"delta":{"content":"sensitive text"}}]}\n\n')
    assert collector.terminal_status is None
    collector.feed(b'data: [DONE]\n\n')
    assert collector.terminal_status == "completed"
    collector.feed(b'data: {"type":"response.incomplete"}\n\n')
    assert collector.terminal_status == "failed"



def test_sse_failure_marker_takes_precedence_over_later_done():
    from auto_router.stream_lifecycle import StreamUsageCollector

    collector = StreamUsageCollector()
    collector.feed(b'data: {"type":"response.failed"}\n\n')
    collector.feed(b'data: [DONE]\n\n')
    assert collector.terminal_status == "failed"
    assert collector.terminal_evidence == "responses_failed"


def test_terminal_event_evidence_is_a_small_allowlisted_metadata_label():
    from auto_router.stream_lifecycle import StreamUsageCollector

    collector = StreamUsageCollector()
    collector.feed(
        b'data: {"type":"response.completed","response":{"output_text":"private"}}\n\n'
    )
    assert collector.terminal_status == "completed"
    assert collector.terminal_evidence == "responses_completed"
    assert "private" not in collector.terminal_evidence
