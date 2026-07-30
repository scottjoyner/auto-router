from __future__ import annotations

from types import SimpleNamespace

from auto_router.models import RouteRequest
from auto_router.strict_assistx_routes import install_strict_assistx_route_guard


def request(*, needs_tools: bool = False) -> RouteRequest:
    return RouteRequest(
        correlation_id="corr-1",
        model="auto/code",
        metadata={"requires_tools": needs_tools},
    )


def test_tool_request_is_blocked_in_router_authority_mode() -> None:
    module = SimpleNamespace(
        _strict_route_guard_installed=False,
        _request_needs_tools=lambda body: bool(body.metadata.get("requires_tools")),
        _select_lane_and_provider=lambda _body, _state: {
            "lane": "paperclip",
            "provider": "agent-jobs",
            "model": "tool_orchestrator",
        },
    )
    install_strict_assistx_route_guard(module)

    selection = module._select_lane_and_provider(request(needs_tools=True), SimpleNamespace())

    assert selection["lane"] == "blocked"
    assert selection["provider"] == "none"
    assert "AssistX" in selection["rationale"]
    assert "Hermes external mode" in selection["rationale"]


def test_nonlocal_selection_is_blocked() -> None:
    module = SimpleNamespace(
        _strict_route_guard_installed=False,
        _request_needs_tools=lambda _body: False,
        _select_lane_and_provider=lambda _body, _state: {
            "lane": "free_api",
            "provider": "cerebras",
            "model": "cloud-model",
        },
    )
    install_strict_assistx_route_guard(module)

    selection = module._select_lane_and_provider(request(), SimpleNamespace())

    assert selection["lane"] == "blocked"
    assert "nonlocal lane" in selection["rationale"]


def test_enabled_local_provider_is_preserved() -> None:
    provider = SimpleNamespace(name="local-runtime", quota_class="local")
    module = SimpleNamespace(
        _strict_route_guard_installed=False,
        _request_needs_tools=lambda _body: False,
        _select_lane_and_provider=lambda _body, _state: {
            "lane": "local",
            "provider": "local-runtime",
            "model": "qwen.gguf",
        },
    )
    install_strict_assistx_route_guard(module)
    state = SimpleNamespace(
        providers=SimpleNamespace(enabled=lambda: [provider]),
    )

    selection = module._select_lane_and_provider(request(), state)

    assert selection["lane"] == "local"
    assert selection["provider"] == "local-runtime"
