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


def _route_status(status_code: int | None, error: Exception | None) -> str:
    if error is not None:
        return "failed"
    if status_code is None:
        return "completed"
    if 200 <= status_code < 400:
        return "completed"
    return "failed"
