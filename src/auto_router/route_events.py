from __future__ import annotations

from typing import Any

from auto_router.event_outbox import EventOutbox, OutboxEvent
from auto_router.models import RouterRequest
from auto_router.settings import get_settings


def ensure_event_outbox(state: Any) -> EventOutbox:
    if not hasattr(state, "event_outbox"):
        state.event_outbox = EventOutbox(get_settings().database_url)
    return state.event_outbox


def enqueue_route_execution_event(
    state: Any,
    request: RouterRequest,
    provider: str,
    model: str,
    stage: str,
    estimate: Any,
    status_code: int | None,
    latency_ms: int,
    usage: dict[str, int] | None = None,
    error: Exception | None = None,
    gateway_metadata: dict[str, Any] | None = None,
) -> str:
    """Queue routing provenance without storing prompt bodies.

    This event intentionally avoids `request.raw_body`, messages, prompt text, or
    response text. It only records operational metadata needed for AssistX/Neo4j
    provenance, quota accounting, debugging, and dashboard history.
    """

    outbox = ensure_event_outbox(state)
    usage = usage or {}
    error_type = type(error).__name__ if error else None
    error_message = str(error)[:1000] if error else None
    status = _route_status(status_code=status_code, error=error)
    event_type = f"router.execution_stage.{status}"
    idempotency_key = (
        f"{event_type}:{request.request_id}:{stage}:{provider}:{model}:"
        f"{status_code}:{error_type or 'none'}"
    )
    context = getattr(state, "context", None)
    gateway_metadata = gateway_metadata if isinstance(gateway_metadata, dict) else {}
    gateway_used = bool(gateway_metadata) or str(provider).startswith("agentgateway")

    payload = {
        "request_id": request.request_id,
        "route": request.route,
        "requested_model": request.model,
        "priority": request.priority.value,
        "profile": request.metadata.get("profile") if isinstance(request.metadata, dict) else None,
        "stage": stage,
        "provider": provider,
        "model": model,
        "status": status,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "input_tokens": int(usage.get("prompt_tokens") or getattr(estimate, "input_tokens", 0)),
        "output_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or getattr(estimate, "total_tokens", 0)),
        "quota_units": getattr(estimate, "dimensions", {}),
        "local_only": request.local_only,
        "allow_cloud": request.allow_cloud,
        "stream": request.stream,
        # Gateway fields (optional)
        "gateway_used": gateway_used,
        "gateway_provider": gateway_metadata.get("provider") if gateway_metadata else (provider if gateway_used else None),
        "upstream_provider": gateway_metadata.get("upstream_provider") if gateway_metadata else None,
        "gateway_latency_ms": gateway_metadata.get("latency_ms") if gateway_metadata else (latency_ms if gateway_used else None),
        # Error fields
        "error_type": error_type,
        "error_message": error_message,
        "context_revision": getattr(context, "revision", "unknown"),
        "context_source": getattr(context, "source", "unknown"),
    }
    return outbox.enqueue(
        OutboxEvent(
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=payload,
        )
    )


def enqueue_route_decision_event(
    state: Any,
    request: RouterRequest,
    profile_name: str,
    stage: str,
    chosen_candidate: Any,
    candidates: list[Any],
    rejections: list[str] | None = None,
) -> str:
    outbox = ensure_event_outbox(state)
    chosen_payload = _candidate_summary(chosen_candidate)
    candidate_payloads = [_candidate_summary(candidate) for candidate in candidates]
    context = getattr(state, "context", None)
    payload = {
        "request_id": request.request_id,
        "route": request.route,
        "requested_model": request.model,
        "profile": profile_name,
        "stage": stage,
        "priority": request.priority.value,
        "local_only": request.local_only,
        "allow_cloud": request.allow_cloud,
        "chosen": chosen_payload,
        "candidates": candidate_payloads,
        "rejections": list(rejections or []),
        "context_revision": getattr(context, "revision", "unknown"),
        "context_source": getattr(context, "source", "unknown"),
    }
    idempotency_key = f"router.route_decision:{request.request_id}:{stage}:{chosen_payload['provider']}:{chosen_payload['model']}"
    return outbox.enqueue(
        OutboxEvent(
            event_type="router.route_decision",
            idempotency_key=idempotency_key,
            payload=payload,
        )
    )


def _candidate_summary(candidate: Any) -> dict[str, Any]:
    provider = getattr(candidate, "provider", None)
    model = getattr(candidate, "model", None)
    return {
        "provider": getattr(provider, "name", getattr(provider, "provider", None)),
        "provider_model": getattr(model, "provider_model", None),
        "model": getattr(model, "alias", getattr(model, "name", None)),
        "score": getattr(candidate, "score", None),
        "reason": getattr(candidate, "reason", ""),
    }


def _route_status(status_code: int | None, error: Exception | None) -> str:
    if error is not None:
        return "failed"
    if status_code is None:
        return "completed"
    if 200 <= status_code < 400:
        return "completed"
    return "failed"
