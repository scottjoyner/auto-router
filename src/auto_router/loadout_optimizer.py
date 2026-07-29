from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Iterable


def estimate_model_ram_gib(model_id: str) -> float:
    """Conservative name-based footprint estimate when manifests lack size data."""
    text = model_id.lower()
    match = re.search(r"(\d+(?:\.\d+)?)\s*b", text)
    params_b = float(match.group(1)) if match else 7.0
    quant_match = re.search(r"q([2-8])", text)
    bits = int(quant_match.group(1)) if quant_match else 5
    # weights plus KV/runtime overhead; intended for simulation, never admission.
    return round(params_b * bits / 8.0 * 1.25 + 2.0, 2)


def simulate_loadout(
    node_reports: Iterable[dict[str, Any]],
    value_matrix: dict[str, Any],
    quality_evidence: dict[str, Any],
    *,
    reserve_fraction: float = 0.20,
) -> dict[str, Any]:
    reports = list(node_reports)
    nodes = {
        str(report.get("hostname") or report.get("ip") or "unknown"): report
        for report in reports
    }
    values = {
        (str(row["node_id"]), str(row["model_id"])): row
        for row in value_matrix.get("entries") or []
    }
    demand: dict[str, int] = {}
    for row in quality_evidence.get("entries") or []:
        family = str(row.get("task_family") or "general")
        demand[family] = demand.get(family, 0) + int(row.get("sample_count") or 0)

    actions: list[dict[str, Any]] = []
    loaded_copies: dict[str, set[str]] = {}
    for node, report in nodes.items():
        for model in report.get("loaded") or []:
            loaded_copies.setdefault(str(model), set()).add(node)

    # Evaluate current residents first.
    for node, report in nodes.items():
        loaded = [str(model) for model in report.get("loaded") or []]
        for model in loaded:
            value = values.get((node, model), {})
            recommendation = str(value.get("recommendation") or "benchmark")
            confidence = float(value.get("confidence") or 0)
            action = "keep"
            reason = recommendation
            if recommendation == "unload_candidate" and confidence >= 0.5:
                alternatives = loaded_copies.get(model, set()) - {node}
                if alternatives and len(loaded) > 1:
                    action = "unload_candidate"
                    reason = "low measured value with redundant loaded coverage"
                else:
                    action = "defer"
                    reason = "protected: last loaded copy or only resident model"
            elif recommendation == "benchmark":
                action, reason = "defer", "collect benchmark evidence before changing loadout"
            actions.append({
                "action": action,
                "node_id": node,
                "model_id": model,
                "estimated_ram_gib": estimate_model_ram_gib(model),
                "effective_rvu_per_hour": value.get("effective_rvu_per_hour"),
                "confidence": confidence,
                "reason": reason,
                "requires_approval": action != "keep",
            })

    # Find valuable loaded models that another node already owns in its library.
    best_by_model: dict[str, dict[str, Any]] = {}
    for row in value_matrix.get("entries") or []:
        model = str(row.get("model_id") or "")
        current = best_by_model.get(model)
        if row.get("recommendation") == "keep_hot" and (
            current is None
            or float(row.get("effective_rvu_per_hour") or 0)
            > float(current.get("effective_rvu_per_hour") or 0)
        ):
            best_by_model[model] = row
    for model, best in best_by_model.items():
        for node, report in nodes.items():
            library = {str(item) for item in report.get("library") or []}
            loaded = {str(item) for item in report.get("loaded") or []}
            if model not in library or model in loaded:
                continue
            specs = report.get("specs") if isinstance(report.get("specs"), dict) else {}
            ram = float(specs.get("ram_gib") or 0)
            used = sum(estimate_model_ram_gib(item) for item in loaded)
            footprint = estimate_model_ram_gib(model)
            headroom = ram * (1.0 - reserve_fraction) - used
            if ram and footprint <= headroom:
                actions.append({
                    "action": "replicate_candidate",
                    "node_id": node,
                    "model_id": model,
                    "estimated_ram_gib": footprint,
                    "effective_rvu_per_hour": best.get("effective_rvu_per_hour"),
                    "confidence": best.get("confidence"),
                    "reason": "high-value model is available locally and fits simulated RAM headroom",
                    "requires_approval": True,
                    "source_node_id": best.get("node_id"),
                })

    priority = {"unload_candidate": 0, "replicate_candidate": 1, "defer": 2, "keep": 3}
    actions.sort(key=lambda row: (priority.get(row["action"], 9), row["node_id"], row["model_id"]))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "simulation",
        "executable": False,
        "constraints": {
            "ram_reserve_fraction": reserve_fraction,
            "protect_last_loaded_copy": True,
            "protect_only_resident_model": True,
            "minimum_unload_confidence": 0.5,
            "model_memory_source": "name_estimate",
        },
        "demand_by_task_family": demand,
        "summary": {
            "nodes": len(nodes),
            "actions": len(actions),
            "keep": sum(row["action"] == "keep" for row in actions),
            "defer": sum(row["action"] == "defer" for row in actions),
            "replicate_candidates": sum(row["action"] == "replicate_candidate" for row in actions),
            "unload_candidates": sum(row["action"] == "unload_candidate" for row in actions),
        },
        "actions": actions,
    }
