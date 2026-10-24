"""Sanitized in-process Auto-Router provider snapshot; never performs a probe.

Use only behind require_admin. This projection is evidence of CURRENT ROUTER
MEMORY, not live model identity, selected route, or inference authorization.
"""

from __future__ import annotations

import hashlib
import json
import time
from urllib.parse import urlsplit


def endpoint_reference(value: object) -> dict[str, object]:
    """Discard userinfo, URL query, fragments, and arbitrary path contents."""
    if not isinstance(value, str) or len(value) > 2048:
        return {"status": "INVALID", "scheme": None, "hostname": None, "port": None}
    try:
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.port is not None
            and not 1 <= parts.port <= 65535
        ):
            raise ValueError("invalid transport")
        return {
            "status": "PARSED_NOT_PROBED",
            "scheme": parts.scheme,
            "hostname": parts.hostname,
            "port": parts.port or (443 if parts.scheme == "https" else 80),
            "userinfo_or_query_present": bool(parts.username or parts.password or parts.query),
        }
    except ValueError:
        return {"status": "INVALID", "scheme": None, "hostname": None, "port": None}


def effective_snapshot(state: object, *, observed_at_ms: int | None = None) -> dict[str, object]:
    """Pure bounded projection; no network, database, or configuration reload."""
    stamp = int(observed_at_ms if observed_at_ms is not None else time.time() * 1000)
    if stamp < 1:
        raise ValueError("invalid capture time")
    registry = getattr(state, "providers", None)
    source = getattr(registry, "providers", None)
    if not isinstance(source, list) or len(source) > 1000:
        raise ValueError("runtime provider registry not initialized or unbounded")

    manager = getattr(state, "runtime_projection_manager", None)
    current = getattr(manager, "current", None) if manager is not None else None
    expiry = getattr(current, "expires_at_ms", None)
    generation = getattr(current, "generation", None)
    if type(expiry) is not int or expiry < stamp:
        freshness = "NO_FRESH_SIGNED_PROJECTION_PROOF"
    else:
        freshness = "PROJECTION_CLAIM_CURRENT_AT_CAPTURE_NOT_PROVIDER_HEALTH"
    projection = {
        "state": freshness,
        "present": current is not None,
        "generation": generation if type(generation) is int else None,
        "revision": str(getattr(current, "revision", ""))[:96] if current else None,
        "expires_at_ms": expiry if type(expiry) is int else None,
    }
    rows = []
    for p in source:
        models = getattr(p, "models", ())
        if not isinstance(models, (list, tuple)) or len(models) > 100:
            raise ValueError("provider model inventory not bounded")
        rows.append(
            {
                "provider_id": str(getattr(p, "id", "") or "")[:128],
                "name": str(getattr(p, "name", "") or "")[:128],
                "type": str(getattr(p, "type", "") or "")[:48],
                "node_id": str(getattr(p, "node_id", "") or "")[:128],
                "enabled_in_router_memory": getattr(p, "enabled", False) is True,
                "effective_base_url": endpoint_reference(getattr(p, "base_url", None)),
                "effective_access_urls": [
                    endpoint_reference(v) for v in (getattr(p, "access_urls", []) or [])[:12]
                ],
                "configured_models": [
                    {
                        "alias": str(getattr(m, "alias", "") or "")[:120],
                        "provider_model": str(getattr(m, "provider_model", "") or "")[:120],
                    }
                    for m in models
                ],
                "resident_model_identity_verified": False,
                "service_reachability_verified": False,
                "admission_granted": False,
            }
        )
    rows.sort(key=lambda x: (x["name"], x["node_id"], x["provider_id"]))
    body = {"providers": rows, "projection": projection}
    source_sha = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    return {
        "schema_version": 1,
        "kind": "EFFECTIVE_ROUTER_MEMORY_READ_ONLY",
        "observed_at_ms": stamp,
        "snapshot_sha256": source_sha,
        "provider_count": len(rows),
        "enabled_count": sum(bool(r["enabled_in_router_memory"]) for r in rows),
        "state_source": "LIVE_ASGI_STATE_NOT_BOOTSTRAP_PROVIDER_FILE",
        "no_probes_or_dispatch": True,
        "may_prove_model_loaded": False,
        "may_modify_routes": False,
        **body,
    }
