"""Gateway metadata builder for agentgateway integration.

Converts route decisions into HTTP headers and optional body metadata
for transmission to the agentgateway sidecar.
"""

from typing import Any, Dict, Optional
import uuid

from auto_router.models import ProviderHealth


PRIVATE_PRIVACY_VALUES = {"private", "local_only"}


def generate_request_id() -> str:
    """Generate a unique request ID for tracing."""
    return str(uuid.uuid4())


def build_gateway_headers(
    *,
    request_id: str,
    profile: str,
    stage: str,
    priority: str,
    privacy: str,
    quota_mode: str,
    provider_plan: str,
    model_plan: str,
    context_revision: Optional[str],
    task_id: Optional[str] = None,
    agent_run_id: Optional[str] = None,
    node_id: Optional[str] = None,
    fallback_allowed: bool = True,
) -> Dict[str, str]:
    """Build HTTP headers for agentgateway request.
    
    Args:
        request_id: Unique request identifier for tracing.
        profile: Routing profile (e.g., auto/code, auto/fast).
        stage: Request stage (e.g., draft, refine, judge).
        priority: Priority level (e.g., high, normal, low).
        privacy: Privacy classification (public, cloud_allowed, local_only, private).
        quota_mode: Quota burn strategy (preserve, balanced, aggressive_burn).
        provider_plan: Provider ID or group.
        model_plan: Model ID.
        context_revision: AssistX context revision or "none".
        task_id: Optional AssistX task ID.
        agent_run_id: Optional future AgentRun ID.
        node_id: Optional node identifier.
        fallback_allowed: Whether gateway can fall back to direct routing.
    
    Returns:
        Dictionary of HTTP headers for the request.
    """
    headers = {
        "x-auto-router-request-id": request_id,
        "x-auto-router-profile": profile,
        "x-auto-router-stage": stage,
        "x-auto-router-priority": priority,
        "x-auto-router-privacy": privacy,
        "x-auto-router-quota-mode": quota_mode,
        "x-auto-router-provider-plan": provider_plan,
        "x-auto-router-model-plan": model_plan,
        "x-auto-router-context-revision": context_revision or "none",
        "x-auto-router-fallback-allowed": str(fallback_allowed).lower(),
        "x-auto-router-local-only": str(privacy in PRIVATE_PRIVACY_VALUES).lower(),
        "x-auto-router-cloud-allowed": str(privacy not in PRIVATE_PRIVACY_VALUES).lower(),
    }

    # Add optional headers if provided
    if task_id:
        headers["x-auto-router-task-id"] = task_id

    if agent_run_id:
        headers["x-auto-router-agent-run-id"] = agent_run_id

    if node_id:
        headers["x-auto-router-node-id"] = node_id

    return headers


def attach_gateway_metadata(
    payload: Dict[str, Any],
    *,
    request_id: str,
    profile: str,
    stage: str,
    priority: str,
    privacy: str,
    quota_mode: str,
    context_revision: Optional[str] = None,
    task_id: Optional[str] = None,
    agent_run_id: Optional[str] = None,
    node_id: Optional[str] = None,
    fallback_allowed: bool = True,
) -> Dict[str, Any]:
    """Attach auto-router metadata to request body.
    
    For gateways that support content-based routing or JSON extraction,
    include a top-level metadata block unless it breaks provider compatibility.
    
    Args:
        payload: Original OpenAI-compatible request payload.
        request_id: Unique request identifier.
        profile: Routing profile.
        stage: Request stage.
        priority: Priority level.
        privacy: Privacy classification.
        quota_mode: Quota burn strategy.
        context_revision: AssistX context revision or None.
        fallback_allowed: Whether gateway can fall back to direct routing.
    
    Returns:
        Updated payload with auto_router metadata block added.
    """
    updated = dict(payload)
    updated["auto_router"] = {
        "request_id": request_id,
        "profile": profile,
        "stage": stage,
        "priority": priority,
        "privacy": privacy,
        "quota_mode": quota_mode,
        "context_revision": context_revision or "none",
        "task_id": task_id,
        "agent_run_id": agent_run_id,
        "node_id": node_id,
        "fallback_allowed": fallback_allowed,
    }
    return updated


def strip_gateway_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Strip auto-router metadata from payload before provider dispatch.
    
    Used when a provider rejects unknown top-level fields.
    
    Args:
        payload: Payload with auto_router metadata block.
    
    Returns:
        Payload with auto_router key removed.
    """
    updated = dict(payload)
    if "auto_router" in updated:
        del updated["auto_router"]
    return updated


def is_privacy_cloud_allowed(privacy: str) -> bool:
    """Check if cloud routing is allowed for given privacy level."""
    return privacy not in PRIVATE_PRIVACY_VALUES


def should_fail_closed_for_private(fail_closed: bool, privacy: str) -> bool:
    """Determine if gateway failure should fail closed for private requests.
    
    Args:
        fail_closed: Configuration flag from GatewayConfig.
        privacy: Privacy classification of the request.
    
    Returns:
        True if request should fail closed on gateway error.
    """
    if not fail_closed:
        return False
    
    return privacy in PRIVATE_PRIVACY_VALUES


async def check_agentgateway_health(base_url: str, timeout_seconds: float = 5.0) -> ProviderHealth:
    """Check health of agentgateway sidecar.
    
    Args:
        base_url: Agentgateway base URL (e.g., http://agentgateway:3000).
        timeout_seconds: Request timeout in seconds.
    
    Returns:
        ProviderHealth with ok=True if gateway is healthy.
    """
    import httpx
    
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            # Try health endpoint first, fall back to root. A 2xx response from
            # either means the gateway is actually serving HTTP (not just a TCP
            # connect). Anything else is treated as down (LLD §3.5 W-62).
            last_exc: Exception | None = None
            for path in ["/health", "/"]:
                try:
                    response = await client.get(f"{base_url.rstrip('/')}{path}")
                except Exception as exc:
                    last_exc = exc
                    continue
                if 200 <= response.status_code < 400:
                    return ProviderHealth(
                        provider="agentgateway",
                        ok=True,
                        detail=f"HTTP {response.status_code}",
                        metadata={"base_url": base_url},
                    )
                last_exc = RuntimeError(f"HTTP {response.status_code}")
            # No endpoint returned success. If we got a response (non-2xx) or a
            # connect error, the gateway is NOT healthy -- do not report ok=True.
            if last_exc is not None:
                return ProviderHealth(
                    provider="agentgateway",
                    ok=False,
                    detail=str(last_exc),
                    metadata={"base_url": base_url},
                )
            return ProviderHealth(
                provider="agentgateway",
                ok=False,
                detail="no response from gateway",
                metadata={"base_url": base_url},
            )
    except httpx.ConnectTimeout:
        return ProviderHealth(
            provider="agentgateway",
            ok=False,
            detail="connection timeout",
            metadata={"base_url": base_url},
        )
    except httpx.ConnectError as exc:
        return ProviderHealth(
            provider="agentgateway",
            ok=False,
            detail=f"connection error: {exc}",
            metadata={"base_url": base_url},
        )
    except Exception as exc:
        return ProviderHealth(
            provider="agentgateway",
            ok=False,
            detail=str(exc),
            metadata={"base_url": base_url},
        )


async def build_agentgateway_status() -> dict[str, Any]:
    """Build a consistent gateway snapshot for health and dashboard surfaces."""
    from auto_router.gateway_config import GatewayConfig

    config = GatewayConfig.from_env()
    snapshot: dict[str, Any] = {
        "enabled": config.enabled and config.mode == "sidecar",
        "mode": config.mode,
        "base_url": config.base_url,
        "openai_base_url": config.openai_base_url,
        "metrics_url": config.metrics_url,
        "fail_open_to_direct": config.fail_open_to_direct,
        "fail_closed_for_private": config.fail_closed_for_private,
        "emit_headers": config.emit_headers,
        "pass_metadata_in_body": config.pass_metadata_in_body,
        "reconcile_usage": config.reconcile_usage,
    }
    if snapshot["enabled"]:
        health = await check_agentgateway_health(config.base_url)
        snapshot.update(
            {
                "ok": health.ok,
                "detail": health.detail,
                "health": health.model_dump(),
            }
        )
    else:
        snapshot.update(
            {
                "ok": True,
                "detail": "not configured",
                "health": None,
            }
        )
    return snapshot
