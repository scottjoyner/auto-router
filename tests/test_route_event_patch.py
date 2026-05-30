from types import SimpleNamespace

from auto_router.context import ContextSnapshot
from auto_router.event_outbox import EventOutbox
from auto_router.models import RouterRequest
from auto_router.route_event_patch import install_route_event_patch


class Estimate:
    input_tokens = 1
    total_tokens = 2
    dimensions = {"rpm": 1}


def test_route_event_patch_wraps_record_usage(tmp_path) -> None:
    calls = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))

    module = SimpleNamespace(
        _record_usage=original,
        state=SimpleNamespace(
            event_outbox=EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}"),
            context=ContextSnapshot(revision="rev", source="unit-test"),
        ),
    )
    request = RouterRequest(request_id="req-patch", route="chat_completions", model="auto/fast")

    install_route_event_patch(module)
    module._record_usage(
        request,
        "local",
        "model",
        "final",
        Estimate(),
        200,
        15,
        {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        None,
    )

    assert len(calls) == 1
    events = module.state.event_outbox.pending()
    assert len(events) == 1
    assert events[0]["event_type"] == "router.execution_stage.completed"
    assert events[0]["payload"]["request_id"] == "req-patch"


def test_route_event_patch_is_idempotent(tmp_path) -> None:
    calls = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))

    module = SimpleNamespace(
        _record_usage=original,
        state=SimpleNamespace(
            event_outbox=EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}"),
            context=ContextSnapshot(),
        ),
    )
    request = RouterRequest(request_id="req-patch", route="chat_completions", model="auto/fast")

    install_route_event_patch(module)
    install_route_event_patch(module)
    module._record_usage(request, "local", "model", "final", Estimate(), 200, 15)

    assert len(calls) == 1
    assert len(module.state.event_outbox.pending()) == 1
