"""Report direct probe health separately from model circuit-open state.

Read-only annotation. Does not relax existing fail-closed provider health,
clear any circuit breaker, or route a model.
"""
from __future__ import annotations

from collections.abc import Collection
from typing import Any


def annotate_health_with_open_model_circuits(
    report: dict[str, Any], provider_name: str, open_owners: Collection[str]
) -> dict[str, Any]:
    """Preserve provider denial while exposing raw probe and model circuit status."""
    prefix = f"{provider_name}/"
    matches = sorted(
        owner for owner in open_owners
        if isinstance(owner, str) and owner.startswith(prefix)
    )
    result = dict(report)
    metadata = result.get("metadata")
    info = dict(metadata) if isinstance(metadata, dict) else {}
    info["transport_probe_ok_before_model_circuit_overlay"] = result.get("ok") is True
    info["open_model_circuit_count"] = len(matches)
    info["model_circuit_overlay_active"] = bool(matches)
    result["metadata"] = info
    if matches:
        result["ok"] = False
        result["detail"] = f"Circuit open: {', '.join(matches[:3])}"
    return result
