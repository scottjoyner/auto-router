"""Local JSONL event sink for cache-affinity shadow observations."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .cache_affinity_shadow import CacheAffinityShadowResult


def cache_affinity_shadow_event(
    result: CacheAffinityShadowResult,
    *,
    request_id: str,
    trace_id: str | None = None,
    model_hash: str | None = None,
    runtime_id: str | None = None,
) -> dict[str, Any]:
    if not request_id.strip():
        raise ValueError("request_id is required")
    return {
        "schema_version": "auto-router.cache-affinity-shadow.v1",
        "timestamp_ms": int(time.time() * 1000),
        "request_id": request_id,
        "trace_id": trace_id,
        "model_hash": model_hash,
        "runtime_id": runtime_id,
        "authoritative_behavior_changed": False,
        "actual_candidate_id": result.actual_candidate_id,
        "affinity_candidate_id": result.affinity_candidate_id,
        "affinity_score": result.affinity_score,
        "agreed": result.agreed,
        "considered": result.considered,
        "excluded_ineligible": result.excluded_ineligible,
    }


def append_cache_affinity_shadow_event(
    event: dict[str, Any],
    path: str | os.PathLike[str] | None = None,
) -> str | None:
    target = str(path or os.getenv("AUTO_ROUTER_CACHE_AFFINITY_SHADOW_JSONL", "")).strip()
    if not target:
        return None
    file_path = Path(target)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    return str(file_path)
