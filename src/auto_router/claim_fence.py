from __future__ import annotations

import hmac
import os
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from .owner_binding import OwnerBindingError, assert_claim_owner


class ExecutorClaimFenceError(RuntimeError):
    pass


def _executor_metadata(request: Any) -> dict[str, Any]:
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
    if (
        type(expected_generation) is not int
        or expected_generation < 1
        or expected_generation != current.generation
    ):
        raise ExecutorClaimFenceError("executor claim projection generation is stale")

    task_id = identity.get("task_id")
    claim_id = identity.get("claim_id")
    agent_id = identity.get("agent_id")
    session_id = identity.get("agent_session_id")
    attempt = identity.get("execution_attempt")
    digest = identity.get("task_payload_digest")
    if (
        any(type(value) is not str or not value for value in (
            task_id, claim_id, agent_id, session_id,
        ))
        or type(attempt) is not int or attempt < 1
        or type(digest) is not str
        or re.fullmatch(r"sha256:[a-f0-9]{64}", digest) is None
    ):
        raise ExecutorClaimFenceError("executor claim revision or digest is incomplete")
    # Also give type-checkers the post-validation evidence; do not coerce an
    # invalid task ID to a string and accidentally admit it.
    assert isinstance(task_id, str)

    base_url = os.getenv("AUTO_ROUTER_EXECUTOR_CLAIM_STATUS_URL", "").strip().rstrip("/")
    service_token = os.getenv("AUTO_ROUTER_ASSISTX_EXECUTOR_SERVICE_TOKEN", "").strip()
    if not base_url or not service_token:
        raise ExecutorClaimFenceError("AssistX executor claim-status service is not configured")
    timeout = max(0.2, float(os.getenv("AUTO_ROUTER_EXECUTOR_CLAIM_TIMEOUT_SECONDS", "2")))
    url = f"{base_url}/api/executor/claims/{quote(task_id, safe='')}/status"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {service_token}"},
            )
    except httpx.HTTPError as exc:
        # Do not reflect URL, upstream exception body or service-token context.
        raise ExecutorClaimFenceError("AssistX claim-status transport failed") from exc
    if response.status_code != 200:
        raise ExecutorClaimFenceError(
            f"AssistX claim-status check returned HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ExecutorClaimFenceError("AssistX claim-status response is not JSON") from exc
    if not isinstance(payload, dict) or payload.get("active") is not True:
        raise ExecutorClaimFenceError(
            str(payload.get("reason") if isinstance(payload, dict) else "claim is not active")
        )
    actual = {
        "task_id": payload.get("task_id"),
        "claim_id": payload.get("claim_id"),
        "agent_id": payload.get("agent_id"),
        "agent_session_id": payload.get("agent_session_id"),
        "execution_attempt": payload.get("execution_attempt"),
        "projection_generation": payload.get("projection_generation"),
    }
    expected = {
        "task_id": task_id,
        "claim_id": claim_id,
        "agent_id": agent_id,
        "agent_session_id": session_id,
        "execution_attempt": attempt,
        "projection_generation": expected_generation,
    }
    if (
        any(type(actual[key]) is not str for key in (
            "task_id", "claim_id", "agent_id", "agent_session_id",
        ))
        or type(actual["execution_attempt"]) is not int
        or type(actual["projection_generation"]) is not int
        or actual != expected
    ):
        raise ExecutorClaimFenceError(
            "AssistX current claim revision no longer matches signed executor token"
        )
    live_digest = payload.get("task_payload_digest")
    if (
        type(live_digest) is not str
        or re.fullmatch(r"sha256:[a-f0-9]{64}", live_digest) is None
        or not hmac.compare_digest(live_digest, digest)
    ):
        raise ExecutorClaimFenceError(
            "AssistX current payload digest differs from signed executor token"
        )
    try:
        assert_claim_owner(identity, payload)
    except OwnerBindingError as exc:
        raise ExecutorClaimFenceError(str(exc)) from exc
    lease_until = payload.get("lease_expires_at_ts")
    if type(lease_until) is not int or lease_until <= int(time.time() * 1000):
        raise ExecutorClaimFenceError("AssistX task lease expired before provider dispatch")
