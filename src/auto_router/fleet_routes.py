#!/usr/bin/env python3
"""Pubsub-style fleet health/status over tailscale.

Each fleet node runs ``fleet_node_reporter.py`` which gathers its local LM Studio
library (``lms ls``), its currently-loaded models, and machine specs, then POSTs
that self-report to ``POST /api/fleet/node-report`` on the router. The router
aggregates the latest report per node, exposes it via ``GET /api/fleet/nodes``
(the fleet-health view), and streams updates via ``GET /api/fleet/stream`` (SSE).

This is the missing visibility layer: the router (and the orchestrator) finally
knows *what each node actually has* — not just what is currently loaded — which is
what lets the orchestrator mount/benchmark models on remote nodes it cannot
enumerate directly.
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/fleet", tags=["fleet"])

# In-process aggregation. One router process, so module-level state is fine.
_node_reports: dict[str, dict[str, Any]] = {}
_sse_subscribers: list[asyncio.Queue] = []
_STALE_SECONDS = 180


def _publish(report: dict[str, Any]) -> None:
    for q in list(_sse_subscribers):
        try:
            q.put_nowait(report)
        except asyncio.QueueFull:
            pass


@router.post("/node-report")
async def node_report(request: Request) -> dict[str, Any]:
    body = await request.json()
    hostname = str(body.get("hostname") or body.get("host_name") or "unknown")
    # Prefer the real connection source IP (the node's tailscale IP) so consumers
    # can match reports to fleet nodes by IP, not just by (sometimes mismatched)
    # hostname. Fall back to an explicitly-sent ip for non-socket transports.
    src_ip = None
    if request.client is not None:
        src_ip = request.client.host
    report = {
        "hostname": hostname,
        "ip": src_ip or body.get("ip"),
        "library": body.get("library") or [],
        "loaded": body.get("loaded") or [],
        "capabilities": body.get("capabilities") or [],
        "specs": body.get("specs") or {},
        "health": body.get("health") or {},
        "os": body.get("os"),
        "received_at": int(time.time()),
    }
    _node_reports[hostname] = report
    _publish(report)
    # Best-effort redis pubsub fan-out for external consumers. ``app.state.redis``
    # is only set when a redis client is configured (see lifespan); guard against
    # it being absent so an in-process-only deployment never crashes (LLD §3.5 W-54).
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await redis.publish("fleet:node-report", _json_dumps(report))
        except Exception:
            pass
    return {"ok": True, "hostname": hostname}


@router.get("/nodes")
async def nodes() -> dict[str, Any]:
    now = int(time.time())
    fresh = {
        h: r for h, r in _node_reports.items()
        if now - r.get("received_at", 0) < _STALE_SECONDS
    }
    return {"generated_at": now, "count": len(fresh), "nodes": list(fresh.values())}


@router.get("/node/{hostname}")
async def node(hostname: str) -> dict[str, Any]:
    return _node_reports.get(hostname, {"error": "no report"})


@router.get("/stream")
async def stream() -> StreamingResponse:
    q: asyncio.Queue = asyncio.Queue(maxsize=64)

    async def event_gen():
        _sse_subscribers.append(q)
        try:
            # Prime with the current snapshot so a new viewer sees state immediately.
            for r in _node_reports.values():
                yield f"data: {_json_dumps(r)}\n\n"
            while True:
                report = await q.get()
                yield f"data: {_json_dumps(report)}\n\n"
        finally:
            if q in _sse_subscribers:
                _sse_subscribers.remove(q)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.get("/network-map")
async def network_map(request: Request) -> dict[str, Any]:
    """Return topology from the AssistX projection plus fresh node reports.

    The router consumes graph state through its existing projection instead of
    opening a second, hard-coded Neo4j connection from the request path.
    """
    router_state = getattr(request.app.state, "router_state", None)
    context = getattr(router_state, "context", None)
    context_nodes = getattr(context, "nodes", []) if context is not None else []
    now = int(time.time())
    nodes: list[dict[str, Any]] = []

    for context_node in context_nodes:
        report = _node_reports.get(context_node.node_id, {})
        received_at = int(report.get("received_at", 0))
        report_is_fresh = received_at > 0 and now - received_at < _STALE_SECONDS
        specs = report.get("specs") if isinstance(report.get("specs"), dict) else {}
        nodes.append(
            {
                "id": context_node.node_id,
                "display_name": context_node.display_name or context_node.node_id,
                "role": context_node.lane.value,
                "online": bool(context_node.running and not context_node.is_blocked),
                "capabilities": sorted(context_node.capabilities),
                "ip": report.get("ip") if report_is_fresh else None,
                "ram_gib": specs.get("ram_gib") if report_is_fresh else None,
                "cpu": specs.get("cpu") if report_is_fresh else None,
                "all_models": report.get("library", []) if report_is_fresh else [],
                "loaded_models": report.get("loaded", []) if report_is_fresh else [],
                "report_received_at": received_at or None,
                "report_fresh": report_is_fresh,
            }
        )

    nodes.sort(key=lambda item: (not item["online"], str(item["id"])))
    online_count = sum(1 for item in nodes if item["online"])
    projection_status = (
        context.projection_status()
        if context is not None and hasattr(context, "projection_status")
        else "missing"
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "assistx-context-projection",
        "projection_status": projection_status,
        "summary": {
            "node_count": len(nodes),
            "online_count": online_count,
            "offline_count": len(nodes) - online_count,
        },
        "nodes": nodes,
    }


def _json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, default=str)
