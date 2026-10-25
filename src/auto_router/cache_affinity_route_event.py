"""Emit cache-affinity shadow evidence beside an authoritative route decision."""

from __future__ import annotations

import os
from typing import Any, Mapping

from .cache_affinity_route_shadow import observe_route_cache_affinity
from .cache_affinity_shadow_event import (
    append_cache_affinity_shadow_event,
    cache_affinity_shadow_event,
)


def emit_route_cache_affinity_shadow(
    *,
    metadata: Mapping[str, Any],
    decision: Mapping[str, Any],
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    """Record an opt-in side-channel observation without modifying `decision`.

    Candidate identifiers supplied by runtime cache telemetry should use the same
    identifier as the authoritative route's target node where possible. When a
    node ID is unavailable the target service is used as the comparison identity.
    """
    actual_candidate_id = str(
        decision.get("target_node_id") or decision.get("target_service") or ""
    ).strip() or None
    observation = observe_route_cache_affinity(
        metadata,
        actual_candidate_id=actual_candidate_id,
    )
    if observation is None:
        return None

    request_id = str(
        decision.get("correlation_id") or decision.get("route_id") or ""
    ).strip()
    if not request_id:
        return None
    envelope = metadata.get("cache_affinity_shadow")
    request_payload = (
        envelope.get("request")
        if isinstance(envelope, Mapping)
        and isinstance(envelope.get("request"), Mapping)
        else {}
    )
    event = cache_affinity_shadow_event(
        observation,
        request_id=request_id,
        trace_id=str(metadata.get("trace_id") or "").strip() or None,
        model_hash=str(request_payload.get("model_hash") or "").strip() or None,
        runtime_id=str(request_payload.get("runtime_id") or "").strip() or None,
    )
    append_cache_affinity_shadow_event(event, path)
    return event
