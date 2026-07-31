from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

import httpx


class ExecutorClaimFenceError(RuntimeError):
    pass


def _executor_metadata(request: Any) -> dict[str, Any] | None:
    metadata = request.metadata if isinstance(getattr(request, "metadata", None), dict) else {}
    service = metadata.get("assistx_service")
    if isinstance(service, dict) and service.get("authenticated") is True:
        return None
    value = metadata.get("assistx_executor")
    if not isinstance(value, dict):
        raise ExecutorClaimFenceError("claim-scoped executor lineage is missing")
    return value


async def assert_executor_claim_current(request: Any, state: Any) -> None:
    """Recheck AssistX authority after admission wait and before network dispatch."""

    identity = _executor_metadata(request)
    if identity is None:
        return
    manager = getattr(state, "runtime_projection_manager", None)
    current = getattr(manager, "current", None)
    if current is None or not current.is_fresh():
        raise ExecutorClaimFenceError("runtime projection is absent or expired")
    expected_generation = int(identity.get("projection_generation") or 0)
    if expected_generation != int(current.generation):
        raise ExecutorClaimFenceError("executor claim projection generation is stale")

    task_id = str(identity.get("task_id") or "").strip()
    claim_id = str(identity.get("claim_id") or "").strip()
    agent_id = str(identity.get("agent_id") or "").strip()
    if not task_id or not claim_id or not agent_id:
        raise ExecutorClaimFenceError("executor claim identity is incomplete")

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
        raise ExecutorClaimFenceError(f"AssistX claim-status check failed: {exc}") from exc
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
        "task_id": str(payload.get("task_id") or ""),
        "claim_id": str(payload.get("claim_id") or ""),
        "agent_id": str(payload.get("agent_id") or ""),
        "projection_generation": int(payload.get("projection_generation") or 0),
    }
    expected = {
        "task_id": task_id,
        "claim_id": claim_id,
        "agent_id": agent_id,
        "projection_generation": expected_generation,
    }
    if actual != expected:
        raise ExecutorClaimFenceError("AssistX active claim no longer matches executor token")
    if int(payload.get("lease_expires_at_ts") or 0) <= int(time.time() * 1000):
        raise ExecutorClaimFenceError("AssistX task lease expired before provider dispatch")
