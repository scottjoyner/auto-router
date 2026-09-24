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
import json
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from auto_router.model_value import build_value_matrix
from auto_router.benchmark_planner import build_benchmark_plan
from auto_router.quality_evidence import aggregate_quality
from auto_router.loadout_optimizer import simulate_loadout
from auto_router.fleet_health import build_health_plan

router = APIRouter(prefix="/api/fleet", tags=["fleet"])

# In-process aggregation. One router process, so module-level state is fine.
_node_reports: dict[str, dict[str, Any]] = {}
_sse_subscribers: list[asyncio.Queue] = []
_STALE_SECONDS = 180


_RUNTIME_KINDS = {
    "lmstudio",
    "lm_studio",
    "llama_cpp",
    "vllm",
    "sglang",
    "openai_compatible",
}
_RUNTIME_PROTOCOLS = {"lmstudio-native", "openai-compatible"}


def _sha256_identity(value: Any) -> bool:
    text = str(value or "")
    if not text.startswith("sha256:") or len(text) != 71:
        return False
    try:
        int(text[7:], 16)
    except ValueError:
        return False
    return True


def _sanitize_runtime_identity_witness(raw: dict[str, Any]) -> dict[str, Any]:
    payload = raw.get("runtime_identity_witness_json")
    signature = raw.get("runtime_identity_witness_signature")
    continuity = raw.get("runtime_identity_continuity")
    if not isinstance(payload, str) or not isinstance(signature, str):
        return {}
    if len(payload.encode("utf-8")) > 16 * 1024 or len(signature.encode("utf-8")) > 8 * 1024:
        return {}
    if "BEGIN SSH SIGNATURE" not in signature or "END SSH SIGNATURE" not in signature:
        return {}
    try:
        witness = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    if not isinstance(witness, dict):
        return {}
    if witness.get("schema_version") != "fleet-runtime-identity-witness.v1":
        return {}
    if witness.get("admission") != {"admitted": False}:
        return {}
    if not all(
        (
            str(witness.get("node_id") or "").strip(),
            str(witness.get("runtime_url") or "").strip(),
            str(witness.get("runtime_kind") or "").strip(),
            str(witness.get("provider_model") or "").strip(),
            _sha256_identity(witness.get("loadout_fingerprint")),
            _sha256_identity(witness.get("model_content_sha256")),
            _sha256_identity(witness.get("witness_fingerprint")),
        )
    ):
        return {}
    if len(str(witness.get("node_id"))) > 128 or len(str(witness.get("provider_model"))) > 256:
        return {}
    parsed = urlparse(str(witness.get("runtime_url") or ""))
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return {}
    process = witness.get("process")
    if not isinstance(process, dict):
        return {}
    try:
        if int(process.get("pid") or 0) <= 0 or int(process.get("process_start_ticks") or 0) <= 0:
            return {}
    except (TypeError, ValueError):
        return {}
    if not _sha256_identity(process.get("executable_sha256")):
        return {}
    if not isinstance(continuity, dict):
        return {}
    try:
        checked_at = int(continuity.get("checked_at") or 0)
        pid = int(continuity.get("pid") or 0)
        start_ticks = int(continuity.get("process_start_ticks") or 0)
    except (TypeError, ValueError):
        return {}
    return {
        "runtime_identity_witness_json": payload,
        "runtime_identity_witness_signature": signature,
        "runtime_identity_continuity": {
            "valid": bool(continuity.get("valid")),
            "reason": str(continuity.get("reason") or "")[:128],
            "checked_at": checked_at,
            "pid": pid,
            "boot_id": str(continuity.get("boot_id") or "")[:128],
            "process_start_ticks": start_ticks,
            "executable_basename": str(
                continuity.get("executable_basename") or ""
            )[:256],
        },
    }


def _sanitize_runtime_observations(value: Any) -> list[dict[str, Any]]:
    """Keep bounded non-admitting runtime evidence from node reporters.

    Fleet reports are visibility/evidence only. A bounded operator-signed
    runtime identity witness may cross this surface, but it remains evidence
    only and can never mint an admitted RuntimeInstance, routing credential, or
    signed AssistX projection.
    """

    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in value[:32]:
        if not isinstance(raw, dict):
            continue
        if raw.get("observation_schema") != "fleet-runtime-observation.v1":
            continue
        observation_id = str(raw.get("runtime_observation_id") or "").strip()
        runtime_kind = str(raw.get("runtime_kind") or "").strip().lower()
        protocol = str(raw.get("protocol") or "").strip().lower()
        base_url = str(raw.get("base_url") or "").strip().rstrip("/")
        if (
            not observation_id
            or len(observation_id) > 128
            or observation_id in seen_ids
            or runtime_kind not in _RUNTIME_KINDS
            or protocol not in _RUNTIME_PROTOCOLS
        ):
            continue
        parsed = urlparse(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or len(base_url) > 512
        ):
            continue
        raw_models = raw.get("models") or []
        models = sorted(
            {
                str(model).strip()
                for model in raw_models[:64]
                if str(model).strip() and len(str(model).strip()) <= 256
            }
        )
        try:
            observed_at = int(raw.get("observed_at") or 0)
        except (TypeError, ValueError):
            observed_at = 0
        seen_ids.add(observation_id)
        witness_evidence = _sanitize_runtime_identity_witness(raw)
        out.append(
            {
                "observation_schema": "fleet-runtime-observation.v1",
                "runtime_observation_id": observation_id,
                "runtime_kind": runtime_kind,
                "protocol": protocol,
                "base_url": base_url,
                "models": models,
                # Never allow a sender to claim readiness for an empty serving
                # set. This remains observation-only evidence.
                "ready": bool(raw.get("ready")) and bool(models),
                "observed_model_count": len(models),
                "models_truncated": isinstance(raw_models, list) and len(raw_models) > 64,
                "observed_at": observed_at,
                **witness_evidence,
                # Explicitly force observation-only semantics even if an
                # untrusted sender attempts to set admitted=true.
                "admitted": False,
            }
        )
    return out


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
    raw_runtimes = body.get("runtimes")
    report = {
        "hostname": hostname,
        # Preserve the transport-observed source separately from any sender
        # supplied IP so operator evidence can distinguish the two.
        "source_ip": src_ip,
        "reported_ip": body.get("ip"),
        "ip": src_ip or body.get("ip"),
        "library": body.get("library") or [],
        "loaded": body.get("loaded") or [],
        "runtimes": _sanitize_runtime_observations(raw_runtimes),
        "runtime_observations_truncated": (
            isinstance(raw_runtimes, list) and len(raw_runtimes) > 32
        ),
        "capabilities": body.get("capabilities") or [],
        "specs": body.get("specs") or {},
        "health": body.get("health") or {},
        "power_profile": str(body.get("power_profile") or "").lower(),
        "power_model_class": str(body.get("power_model_class") or "").lower(),
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
                "runtime_observation_count": (
                    len(report.get("runtimes") or []) if report_is_fresh else 0
                ),
                "runtime_models": (
                    sorted(
                        {
                            str(model)
                            for runtime in (report.get("runtimes") or [])
                            if isinstance(runtime, dict) and runtime.get("ready")
                            for model in (runtime.get("models") or [])
                        }
                    )
                    if report_is_fresh
                    else []
                ),
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


@router.get("/value-matrix")
async def value_matrix(request: Request, limit: int = 1000) -> dict[str, Any]:
    """Return advisory model economics based on live reports and runtime evidence."""
    router_state = getattr(request.app.state, "router_state", None)
    ledger = getattr(router_state, "ledger", None)
    samples = ledger.recent_runtime_samples(limit=max(1, min(limit, 5000))) if ledger else []
    memory_store = getattr(router_state, "memory_store", None)
    outcomes = memory_store.recent_outcomes(limit=limit) if memory_store else []
    quality = aggregate_quality(outcomes)
    result = build_value_matrix(_node_reports.values(), samples, quality)
    result["generated_at"] = datetime.now(UTC).isoformat()
    return result


@router.get("/quality-evidence")
async def quality_evidence(request: Request, limit: int = 1000) -> dict[str, Any]:
    router_state = getattr(request.app.state, "router_state", None)
    memory_store = getattr(router_state, "memory_store", None)
    outcomes = memory_store.recent_outcomes(limit=limit) if memory_store else []
    return aggregate_quality(outcomes)


@router.get("/benchmark-plan")
async def benchmark_plan(request: Request, limit: int = 1000) -> dict[str, Any]:
    router_state = getattr(request.app.state, "router_state", None)
    ledger = getattr(router_state, "ledger", None)
    memory_store = getattr(router_state, "memory_store", None)
    samples = ledger.recent_runtime_samples(limit=limit) if ledger else []
    quality = aggregate_quality(memory_store.recent_outcomes(limit=limit) if memory_store else [])
    matrix = build_value_matrix(_node_reports.values(), samples, quality)
    return build_benchmark_plan(matrix, quality)


@router.get("/routing-regret")
async def routing_regret(request: Request, limit: int = 100) -> dict[str, Any]:
    router_state = getattr(request.app.state, "router_state", None)
    ledger = getattr(router_state, "ledger", None)
    if ledger is None:
        return {"summary": {"decisions": 0, "completed": 0}, "items": []}
    return ledger.counterfactual_summary(limit=limit)


@router.get("/loadout-simulation")
async def loadout_simulation(request: Request, limit: int = 1000) -> dict[str, Any]:
    router_state = getattr(request.app.state, "router_state", None)
    ledger = getattr(router_state, "ledger", None)
    memory_store = getattr(router_state, "memory_store", None)
    samples = ledger.recent_runtime_samples(limit=limit) if ledger else []
    quality = aggregate_quality(memory_store.recent_outcomes(limit=limit) if memory_store else [])
    matrix = build_value_matrix(_node_reports.values(), samples, quality)
    return simulate_loadout(_node_reports.values(), matrix, quality)


@router.get("/health-plan")
async def health_plan(request: Request, limit: int = 500) -> dict[str, Any]:
    router_state = getattr(request.app.state, "router_state", None)
    ledger = getattr(router_state, "ledger", None)
    memory_store = getattr(router_state, "memory_store", None)
    samples = ledger.recent_runtime_samples(limit=limit) if ledger else []
    quality = aggregate_quality(memory_store.recent_outcomes(limit=limit) if memory_store else [])
    matrix = build_value_matrix(_node_reports.values(), samples, quality)
    topology = await network_map(request)
    return build_health_plan(topology, matrix, samples)


def _json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, default=str)
