from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable

TARGET_OUTPUT_TOKENS = 2048
UTILITY_SCALE = 100.0
CONFIDENCE_SAMPLE_TARGET = 12


def response_utility(tokens: int = TARGET_OUTPUT_TOKENS) -> float:
    """Diminishing-return utility for a response of ``tokens`` tokens."""
    return UTILITY_SCALE * (1.0 - math.exp(-max(tokens, 0) / TARGET_OUTPUT_TOKENS))


def build_value_matrix(
    node_reports: Iterable[dict[str, Any]],
    runtime_samples: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Build an advisory opportunity-cost matrix from reports and measurements.

    Provider IDs are matched to node hostname/IP. Unknown providers remain visible
    so remote/cloud measurements are not silently discarded.
    """
    reports = list(node_reports)
    aliases: dict[str, str] = {}
    inventory: dict[str, dict[str, Any]] = {}
    for report in reports:
        node = str(report.get("hostname") or report.get("ip") or "unknown")
        inventory[node] = report
        aliases[node.lower()] = node
        if report.get("ip"):
            aliases[str(report["ip"]).lower()] = node

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for sample in runtime_samples:
        provider = str(sample.get("provider_id") or "unknown")
        node = aliases.get(provider.lower(), provider)
        model = str(sample.get("model_id") or "unknown")
        grouped[(node, model)].append(sample)

    for node, report in inventory.items():
        for model in report.get("loaded") or []:
            grouped.setdefault((node, str(model)), [])

    utility = response_utility()
    rows: list[dict[str, Any]] = []
    for (node, model), samples in grouped.items():
        successes = [
            row for row in samples
            if not row.get("error_type")
            and (row.get("status_code") is None or int(row["status_code"]) < 400)
        ]
        tps_values = [
            float(row["tokens_per_second"]) for row in successes
            if row.get("tokens_per_second") is not None
            and float(row["tokens_per_second"]) >= 0
        ]
        value_values = [
            float(row["value_per_second"]) * 3600.0 for row in successes
            if row.get("value_per_second") is not None
        ]
        count = len(samples)
        confidence = min(1.0, count / CONFIDENCE_SAMPLE_TARGET)
        success_rate = len(successes) / count if count else None
        tps = sum(tps_values) / len(tps_values) if tps_values else None
        raw_rvu_hour = (
            utility * 3600.0 * tps / TARGET_OUTPUT_TOKENS
            if tps is not None else None
        )
        # Runtime value already incorporates the router's request valuation. If it
        # is absent, use the deterministic token-utility baseline.
        measured_rvu_hour = (
            sum(value_values) / len(value_values) if value_values else raw_rvu_hour
        )
        reliability = success_rate if success_rate is not None else 0.5
        effective = (
            measured_rvu_hour * reliability * (0.5 + 0.5 * confidence)
            if measured_rvu_hour is not None else None
        )
        report = inventory.get(node, {})
        loaded = model in set(str(item) for item in report.get("loaded") or [])
        rows.append({
            "node_id": node,
            "model_id": model,
            "loaded": loaded,
            "online": bool(report),
            "sample_count": count,
            "confidence": round(confidence, 3),
            "success_rate": round(success_rate, 3) if success_rate is not None else None,
            "tokens_per_second": round(tps, 3) if tps is not None else None,
            "rvu_per_hour": round(measured_rvu_hour, 2) if measured_rvu_hour is not None else None,
            "effective_rvu_per_hour": round(effective, 2) if effective is not None else None,
            "source": "runtime" if count else "reported-loaded",
        })

    measured = [row["effective_rvu_per_hour"] for row in rows if row["effective_rvu_per_hour"] is not None]
    best = max(measured, default=None)
    for row in rows:
        effective = row["effective_rvu_per_hour"]
        row["opportunity_cost_rvu_per_hour"] = (
            round(best - effective, 2) if best is not None and effective is not None else None
        )
        if row["sample_count"] < 3:
            recommendation, reason = "benchmark", "insufficient runtime evidence"
        elif (row["success_rate"] or 0) < 0.7:
            recommendation, reason = "unload_candidate", "success rate is below 70%"
        elif best and effective is not None and effective >= best * 0.8:
            recommendation, reason = "keep_hot", "within 20% of the fleet's best effective value"
        elif best and effective is not None and effective < best * 0.35:
            recommendation, reason = "unload_candidate", "below 35% of the fleet's best effective value"
        else:
            recommendation, reason = "opportunistic", "use when fit or locality outweighs throughput"
        row["recommendation"] = recommendation
        row["reason"] = reason

    order = {"keep_hot": 0, "benchmark": 1, "opportunistic": 2, "unload_candidate": 3}
    rows.sort(key=lambda row: (order[row["recommendation"]], -(row["effective_rvu_per_hour"] or -1)))
    return {
        "method": {
            "target_output_tokens": TARGET_OUTPUT_TOKENS,
            "utility": round(utility, 3),
            "confidence_samples": CONFIDENCE_SAMPLE_TARGET,
            "advisory_only": True,
        },
        "summary": {
            "entries": len(rows),
            "measured": len(measured),
            "keep_hot": sum(row["recommendation"] == "keep_hot" for row in rows),
            "benchmark": sum(row["recommendation"] == "benchmark" for row in rows),
            "unload_candidates": sum(row["recommendation"] == "unload_candidate" for row in rows),
        },
        "entries": rows,
    }
