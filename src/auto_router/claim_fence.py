from __future__ import annotations

import math
import os
import time
from typing import Any
from urllib.parse import quote

import httpx

from .owner_binding import OwnerBindingError, assert_claim_owner


class ExecutorClaimFenceError(RuntimeError):
    pass


class ExecutorClaimStatusUnavailable(ExecutorClaimFenceError):
    pass


class ExecutorClaimStatusCoolingDown(ExecutorClaimStatusUnavailable):
    def __init__(self, retry_after: int):
        super().__init__("AssistX claim-status authority retry cooldown")
        self.retry_after = retry_after


_CLAIM_STATUS_COOLDOWN_SECONDS = 5.0


def _mark_authority_outage(state: Any) -> None:
    state.claim_status_retry_not_before = time.monotonic() + _CLAIM_STATUS_COOLDOWN_SECONDS


def _check_authority_cooldown(state: Any) -> None:
    remaining = getattr(state, "claim_status_retry_not_before", 0.0) - time.monotonic()
    if remaining > 0:
        state.claim_status_checks_suppressed = getattr(state, "claim_status_checks_suppressed", 0) + 1
        raise ExecutorClaimStatusCoolingDown(max(1, math.ceil(remaining)))


def _executor_metadata(request: Any) -> dict[str, Any] | None:
    metadata = request.metadata if isinstance(getattr(request, "metadata", None), dict) else {}
    # Caller-controlled model request metadata is not a trusted service
    # identity. Do not interpret an "authenticated" boolean as a lease fence:
    # upstream middleware must use a signed executor token instead.
    if "assistx_service" in metadata:
        raise ExecutorClaimFenceError("untrusted service identity cannot bypass claim fence")
    value = metadata.get("assistx_executor")
    if not isinstance(value, dict):
        raise ExecutorClaimFenceError("claim-scoped executor lineage is missing")
    return value


async def assert_executor_claim_current(request: Any, state: Any) -> None:
    """Recheck AssistX authority after admission wait and before network dispatch."""

    identity = _executor_metadata(request)
    manager = getattr(state, "runtime_projection_manager", None)
    current = getattr(manager, "current", None)
    if current is None or not current.is_fresh():
        raise ExecutorClaimFenceError("runtime projection is absent or expired")
    expected_generation = identity.get("projection_generation")
    if type(expected_generation) is not int or expected_generation <= 0:
        raise ExecutorClaimFenceError("executor claim projection generation is invalid")
    if expected_generation != current.generation:
        raise ExecutorClaimFenceError("executor claim projection generation is stale")

    task_id = str(identity.get("task_id") or "").strip()
    claim_id = str(identity.get("claim_id") or "").strip()
    agent_id = str(identity.get("agent_id") or "").strip()
    if not task_id or not claim_id or not agent_id:
        raise ExecutorClaimFenceError("executor claim identity is incomplete")

    _check_authority_cooldown(state)
    base_url = os.getenv("AUTO_ROUTER_EXECUTOR_CLAIM_STATUS_URL", "").strip().rstrip("/")
    service_token = os.getenv("AUTO_ROUTER_EXECUTOR_CLAIM_STATUS_TOKEN", "").strip()
    if not base_url or not service_token:
        _mark_authority_outage(state)
        raise ExecutorClaimStatusUnavailable("AssistX executor claim-status service is not configured")
    timeout = max(0.2, float(os.getenv("AUTO_ROUTER_EXECUTOR_CLAIM_TIMEOUT_SECONDS", "2")))
    url = f"{base_url}/api/executor/claims/{quote(task_id, safe='')}/status"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {service_token}"},
            )
    except httpx.HTTPError as exc:
        _mark_authority_outage(state)
        raise ExecutorClaimStatusUnavailable("AssistX claim-status transport failed") from exc
    if response.status_code != 200:
        if response.status_code in (401, 403, 429) or response.status_code >= 500:
            _mark_authority_outage(state)
        raise ExecutorClaimStatusUnavailable(
            f"AssistX claim-status check returned HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        _mark_authority_outage(state)
        raise ExecutorClaimStatusUnavailable("AssistX claim-status response is not JSON") from exc
    if not isinstance(payload, dict) or type(payload.get("active")) is not bool:
        _mark_authority_outage(state)
        raise ExecutorClaimStatusUnavailable("AssistX claim-status response is malformed")
    if payload.get("active") is not True:
        raise ExecutorClaimFenceError(
            str(payload.get("reason") if isinstance(payload, dict) else "claim is not active")
        )
    generation = payload.get("projection_generation")
    expiry = payload.get("lease_expires_at_ts")
    if type(generation) is not int or generation <= 0 or type(expiry) is not int or expiry <= 0:
        _mark_authority_outage(state)
        raise ExecutorClaimStatusUnavailable("AssistX active claim response has invalid generation or expiry")
    if any(type(payload.get(key)) is not str or not payload[key] for key in ("task_id", "claim_id", "agent_id")):
        _mark_authority_outage(state)
        raise ExecutorClaimStatusUnavailable("AssistX active claim response has invalid identity")
    actual = {
        "task_id": str(payload.get("task_id") or ""),
        "claim_id": str(payload.get("claim_id") or ""),
        "agent_id": str(payload.get("agent_id") or ""),
        "projection_generation": generation,
    }
    expected = {
        "task_id": task_id,
        "claim_id": claim_id,
        "agent_id": agent_id,
        "projection_generation": expected_generation,
    }
    if actual != expected:
        raise ExecutorClaimFenceError("AssistX active claim no longer matches executor token")
    try:
        assert_claim_owner(identity, payload)
    except OwnerBindingError as exc:
        raise ExecutorClaimFenceError(str(exc)) from exc
    if expiry <= int(time.time() * 1000):
        raise ExecutorClaimFenceError("AssistX task lease expired before provider dispatch")
