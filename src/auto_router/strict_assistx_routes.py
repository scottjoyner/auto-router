from __future__ import annotations

from typing import Any

from auto_router.models import RouteRequest


def install_strict_assistx_route_guard(assistx_routes_module: Any) -> None:
    """Keep the AssistX route-decision endpoint within router authority.

    The reconciled router may choose a local inference policy. It may not create an
    agent job, dispatch Paperclip/Hermes, select a free/paid lane, or become a task
    executor. Tool-capable work is assigned by AssistX and executed by Hermes through
    its external auto-router provider configuration.
    """

    if getattr(assistx_routes_module, "_strict_route_guard_installed", False):
        return
    original = assistx_routes_module._select_lane_and_provider

    def guarded(request: RouteRequest, state: Any) -> dict[str, Any]:
        if assistx_routes_module._request_needs_tools(request):
            return {
                "lane": "blocked",
                "provider": "none",
                "model": "none",
                "target_service": None,
                "target_node_id": None,
                "rationale": (
                    "Tool-capable work must be assigned by AssistX and executed by "
                    "Hermes external mode; auto-router does not create agent jobs"
                ),
                "confidence": 0.0,
            }
        selection = original(request, state)
        lane = str(selection.get("lane") or "").strip().lower()
        provider_name = str(selection.get("provider") or "").strip()
        if lane != "local":
            return {
                "lane": "blocked",
                "provider": "none",
                "model": "none",
                "target_service": None,
                "target_node_id": None,
                "rationale": (
                    f"Strict-offline reconciliation rejected nonlocal lane "
                    f"{lane or 'unknown'}"
                ),
                "confidence": 0.0,
            }
        registry = getattr(state, "providers", None)
        configured = [] if registry is None else registry.enabled()
        provider = next(
            (
                item
                for item in configured
                if str(getattr(item, "name", "")).strip() == provider_name
            ),
            None,
        )
        if provider is None or str(getattr(provider, "quota_class", "")).lower() != "local":
            return {
                "lane": "blocked",
                "provider": "none",
                "model": "none",
                "target_service": None,
                "target_node_id": None,
                "rationale": "Selected provider is not an enabled local runtime",
                "confidence": 0.0,
            }
        return selection

    assistx_routes_module._select_lane_and_provider = guarded
    assistx_routes_module._strict_route_guard_installed = True
