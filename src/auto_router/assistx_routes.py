from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from auto_router.event_outbox import EventOutbox, OutboxEvent
from auto_router.models import RouteDecision, RouteRequest
from auto_router.route_events import ensure_event_outbox

router = APIRouter(tags=["assistx-routes"])


def _build_route_decision(
    request: RouteRequest,
    *,
    lane: str,
    provider: str,
    model: str,
    target_service: str | None = None,
    target_node_id: str | None = None,
    rationale: str = "",
    confidence: float = 0.9,
    status: str = "selected",
) -> dict[str, Any]:
    route_id = f"route:{uuid.uuid4().hex[:12]}"
    event_type = f"route.{status}"
    return RouteDecision(
        event_type=event_type,
        correlation_id=request.correlation_id,
        route_id=route_id,
        task_id=request.task_id,
        lane=lane,
        provider=provider,
        model=model,
        target_service=target_service,
        target_node_id=target_node_id,
        rationale=rationale,
        confidence=confidence,
    ).model_dump()


def _select_lane_and_provider(
    request: RouteRequest,
    state: Any,
) -> dict[str, Any]:
    """Use existing PolicyEngine context to select lane/model/provider."""
    context = getattr(state, "context", None)
    providers = getattr(state, "providers", None)
    if not context or not providers:
        return {
            "lane": "local",
            "provider": "lmstudio",
            "model": "local/default",
            "target_service": None,
            "target_node_id": None,
            "rationale": "No context available; defaulting to local",
            "confidence": 0.3,
        }

    enabled = providers.enabled()
    local_providers = [p for p in enabled if getattr(p, "quota_class", "") == "local" or (getattr(p, "base_url", "") or "").startswith("http://100.")]

    if local_providers:
        best = local_providers[0]
        models = getattr(best, "models", [])
        model_name = models[0].alias if models else "local/default"
        model_provider = models[0].provider_model if models else model_name
        return {
            "lane": "local",
            "provider": best.name,
            "model": model_provider,
            "target_service": f"{best.name}:{model_provider}",
            "target_node_id": getattr(best, "node_id", None),
            "rationale": f"Selected local provider {best.name} with model {model_provider}",
            "confidence": 0.85,
        }

    free_providers = [p for p in enabled if "free" in str(getattr(p, "quota_class", ""))]
    if free_providers:
        best = free_providers[0]
        models = getattr(best, "models", [])
        model_name = models[0].alias if models else "free/default"
        model_provider = models[0].provider_model if models else model_name
        return {
            "lane": "free_api",
            "provider": best.name,
            "model": model_provider,
            "target_service": f"{best.name}:{model_provider}",
            "target_node_id": getattr(best, "node_id", None),
            "rationale": f"Selected free provider {best.name} with model {model_provider}",
            "confidence": 0.7,
        }

    return {
        "lane": "blocked",
        "provider": "none",
        "model": "none",
        "target_service": None,
        "target_node_id": None,
        "rationale": "No eligible provider found",
        "confidence": 0.0,
    }


def register_assistx_routes(app: Any, state: Any) -> None:
    @app.post("/api/routes/request")
    async def route_request(body: RouteRequest) -> dict[str, Any]:
        selection = _select_lane_and_provider(body, state)
        lane = selection["lane"]

        if lane == "blocked":
            decision = _build_route_decision(
                body,
                lane="blocked",
                provider="none",
                model="none",
                rationale=selection["rationale"],
                confidence=0.0,
                status="blocked",
            )
        elif lane in ("local", "free_api", "paid_api", "heavy_reasoning"):
            decision = _build_route_decision(
                body,
                lane=lane,
                provider=selection["provider"],
                model=selection["model"],
                target_service=selection.get("target_service"),
                target_node_id=selection.get("target_node_id"),
                rationale=selection["rationale"],
                confidence=selection["confidence"],
                status="selected",
            )
        else:
            decision = _build_route_decision(
                body,
                lane=lane,
                provider=selection["provider"],
                model=selection["model"],
                rationale=selection["rationale"],
                confidence=selection["confidence"],
                status="failed",
            )

        outbox = ensure_event_outbox(state)
        outbox.enqueue(
            OutboxEvent(
                event_type=decision["event_type"],
                idempotency_key=f"{decision['event_type']}:{body.correlation_id}:{decision['route_id']}",
                payload=decision,
            )
        )

        return decision
