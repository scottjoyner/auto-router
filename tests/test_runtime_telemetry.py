from __future__ import annotations

from types import SimpleNamespace

from auto_router.event_outbox import OutboxEvent
from auto_router.models import ModelConfig, ProviderConfig, QuotaEstimate, RouterRequest
from auto_router.route_events import enqueue_route_execution_event


class FakeOutbox:
    def __init__(self) -> None:
        self.event: OutboxEvent | None = None

    def enqueue(self, event: OutboxEvent) -> str:
        self.event = event
        return "event-1"


def test_provider_and_model_accept_runtime_identity_fields() -> None:
    provider = ProviderConfig(
        name="xwing",
        type="lmstudio",
        node_id="xwing",
        runtime_instance_id="lmstudio-xwing-1234",
        runtime_kind="lmstudio",
        runtime_version="0.4.7",
        headless=False,
        parallel_slots=1,
        base_url="http://192.168.1.9:1234/v1",
        models=[
            ModelConfig(
                alias="local/qwen",
                provider_model="qwen.gguf",
                model_instance_id="model-instance-1",
                artifact_fingerprint="sha256:abc",
                quantization="Q4_K_M",
                context_window=32768,
            )
        ],
    )

    assert provider.runtime_instance_id == "lmstudio-xwing-1234"
    assert provider.runtime_version == "0.4.7"
    assert provider.headless is False
    assert provider.models[0].quantization == "Q4_K_M"


def test_route_execution_event_contains_runtime_path_and_model_telemetry(monkeypatch) -> None:
    outbox = FakeOutbox()
    monkeypatch.setattr("auto_router.route_events.ensure_event_outbox", lambda _state: outbox)
    state = SimpleNamespace(context=None)
    request = RouterRequest(
        request_id="request-1",
        route="chat_completions",
        model="local/qwen",
        task_id="task-1",
        local_only=True,
        allow_cloud=False,
        metadata={
            "task_title": "Backtest BTC breakout strategy",
            "repository": "portfolio-management",
            "agent": "hermes-local",
            "runtime_node_id": "xwing",
            "runtime_instance_id": "lmstudio-xwing-1234",
            "runtime_kind": "lmstudio",
            "runtime_version": "0.4.7",
            "headless": False,
            "selected_transport": "lan",
            "selected_access_url": "http://192.168.1.9:1234/v1",
            "parallel_slots": 1,
            "queue_limit": 4,
            "model_instance_id": "model-instance-1",
            "model_key": "local/qwen",
            "artifact_fingerprint": "sha256:abc",
            "quantization": "Q4_K_M",
            "context_length": 32768,
        },
    )

    event_id = enqueue_route_execution_event(
        state,
        request,
        provider="reconciliation-local-runtime",
        model="qwen.gguf",
        stage="final",
        estimate=QuotaEstimate(input_tokens=100, total_tokens=150),
        status_code=200,
        latency_ms=2000,
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        started_at_ms=1000,
        ended_at_ms=3000,
        queue_wait_ms=12,
        load_time_ms=210,
        tokens_per_second=8.5,
    )

    assert event_id == "event-1"
    assert outbox.event is not None
    payload = outbox.event.payload
    assert payload["task_title"] == "Backtest BTC breakout strategy"
    assert payload["runtime_node_id"] == "xwing"
    assert payload["runtime_instance_id"] == "lmstudio-xwing-1234"
    assert payload["selected_transport"] == "lan"
    assert payload["selected_access_url"] == "http://192.168.1.9:1234/v1"
    assert payload["model_instance_id"] == "model-instance-1"
    assert payload["quantization"] == "Q4_K_M"
    assert payload["time_to_first_token_ms"] == 210
    assert payload["tokens_per_second"] == 8.5
    assert payload["input_tokens"] == 100
    assert payload["output_tokens"] == 50
