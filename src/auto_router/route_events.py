from __future__ import annotations

from typing import Any

from auto_router.event_outbox import EventOutbox, OutboxEvent
from auto_router.models import RouterRequest
from auto_router.settings import get_settings
from auto_router.signal_registry import route_decision_signals, signal_snapshot


def ensure_event_outbox(state: Any) -> EventOutbox:
    if not hasattr(state, "event_outbox"):
        state.event_outbox = EventOutbox(get_settings().database_url)
    return state.event_outbox


def _provider_node_id(state: Any, provider_name: str | None) -> str | None:
    if not provider_name or not hasattr(state, "providers"):
        return None
    context = getattr(state, "context", None)
    canonical = getattr(context, "canonical_provider_name", lambda value: str(value).strip().lower())(provider_name)
    if context is not None and hasattr(context, "provider_for"):
        provider = context.provider_for(canonical)
        if provider is not None:
            return provider.node_id
    for provider in state.providers.enabled():
        provider_name_value = getattr(context, "canonical_provider_name", lambda value: str(value).strip().lower())(provider.name)
        if provider_name_value == canonical:
            return provider.node_id
    return None


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
    started_at_ms: int | None = None,
    ended_at_ms: int | None = None,
    queue_wait_ms: int | None = None,
    load_time_ms: int | None = None,
    tokens_per_second: float | None = None,
    value_units: int | None = None,
    value_per_second: float | None = None,
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
    context = getattr(state, "context", None)
    canonical_provider = getattr(context, "canonical_provider_name", lambda value: str(value).strip().lower())(provider)
    canonical_model = getattr(context, "canonical_model_id", lambda value: str(value).strip().lower())(model)
    provider_model_id = _provider_scoped_model_id(canonical_provider, canonical_model) if provider and model else None
    status = _route_status(status_code=status_code, error=error)
    event_type = f"router.execution_stage.{status}"
    idempotency_key = (
        f"{event_type}:{request.request_id}:{stage}:{canonical_provider}:{canonical_model}:"
        f"{status_code}:{error_type or 'none'}"
    )
    gateway_metadata = gateway_metadata if isinstance(gateway_metadata, dict) else {}
    gateway_used = bool(gateway_metadata) or str(provider).startswith("agentgateway")
    request_metadata = request.metadata if isinstance(request.metadata, dict) else {}
    task_id = getattr(request, "task_id", None) or request_metadata.get("task_id")
    agent_run_id = getattr(request, "agent_run_id", None) or request_metadata.get("agent_run_id")
    node_id = getattr(request, "node_id", None) or request_metadata.get("node_id")

    payload = {
        "request_id": request.request_id,
        "route": request.route,
        "requested_model": request.model,
        "priority": request.priority.value,
        "profile": request_metadata.get("profile"),
        "task_id": task_id,
        "agent_run_id": agent_run_id,
        "node_id": node_id,
        "stage": stage,
        "provider": provider,
        "provider_id": canonical_provider,
        "model": model,
        "provider_model_id": provider_model_id,
        "status": status,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "started_at_ms": started_at_ms,
        "ended_at_ms": ended_at_ms,
        "queue_wait_ms": queue_wait_ms,
        "load_time_ms": load_time_ms,
        "tokens_per_second": tokens_per_second,
        "value_units": value_units,
        "value_per_second": value_per_second,
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
    request_metadata = request.metadata if isinstance(request.metadata, dict) else {}
    task_id = getattr(request, "task_id", None) or request_metadata.get("task_id")
    agent_run_id = getattr(request, "agent_run_id", None) or request_metadata.get("agent_run_id")
    node_id = getattr(request, "node_id", None) or request_metadata.get("node_id")
    payload = {
        "request_id": request.request_id,
        "route": request.route,
        "requested_model": request.model,
        "profile": profile_name,
        "task_id": task_id,
        "agent_run_id": agent_run_id,
        "node_id": node_id,
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
    idempotency_key = f"router.route_decision:{request.request_id}:{stage}:{chosen_payload['provider_id']}:{chosen_payload['model_id'] or chosen_payload['model']}"
    event_id = outbox.enqueue(
        OutboxEvent(
            event_type="router.route_decision",
            idempotency_key=idempotency_key,
            payload=payload,
        )
    )
    if hasattr(state, "signal_registry"):
        node_id = _provider_node_id(state, chosen_payload.get("provider_id") or chosen_payload.get("provider"))
        signals = route_decision_signals(
            request=request,
            profile_name=profile_name,
            stage=stage,
            chosen=chosen_payload,
            candidates=candidate_payloads,
            rejections=rejections,
            node_id=node_id,
        )
        if signals:
            state.signal_registry.save_snapshot(signal_snapshot(signals, revision=f"route-decision:{request.request_id}:{stage}", source="route_decision"))
            state.context = state.signal_registry.hydrate_context(state.context)
            if hasattr(state, "policy_engine"):
                state.policy_engine.context = state.context
    return event_id


def _candidate_summary(candidate: Any) -> dict[str, Any]:
    provider = getattr(candidate, "provider", None)
    model = getattr(candidate, "model", None)
    provider_name = str(getattr(provider, "name", getattr(provider, "provider", None)) or "").strip()
    provider_id = provider_name.lower() or None
    provider_model = str(getattr(model, "provider_model", None) or getattr(model, "alias", getattr(model, "name", None)) or "").strip()
    model_id = f"{provider_id}.{provider_model.lower()}" if provider_id and provider_model else None
    return {
        "provider": provider_name,
        "provider_id": provider_id,
        "provider_model": getattr(model, "provider_model", None),
        "model": str(getattr(model, "alias", getattr(model, "name", None)) or "").strip(),
        "model_id": model_id,
        "score": getattr(candidate, "score", None),
        "reason": getattr(candidate, "reason", ""),
    }


def _provider_scoped_model_id(provider: str, model_id: str) -> str:
    provider_id = str(provider or "").strip().lower()
    model_key = str(model_id or "").strip().lower()
    if not model_key:
        return provider_id
    if not provider_id or model_key.startswith(f"{provider_id}."):
        return model_key
    return f"{provider_id}.{model_key}"


def _route_status(status_code: int | None, error: Exception | None) -> str:
    if error is not None:
        return "failed"
    if status_code is None:
        return "completed"
    if 200 <= status_code < 400:
        return "completed"
    return "failed"
