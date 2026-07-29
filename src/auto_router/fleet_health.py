from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable


def build_health_plan(
    network_map: dict[str, Any],
    value_matrix: dict[str, Any],
    runtime_samples: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    samples = list(runtime_samples)
    values_by_node: dict[str, list[dict[str, Any]]] = {}
    for row in value_matrix.get("entries") or []:
        values_by_node.setdefault(str(row.get("node_id") or "unknown"), []).append(row)
    errors_by_node: dict[str, int] = {}
    for row in samples:
        if row.get("error_type") or (
            row.get("status_code") is not None and int(row["status_code"]) >= 400
        ):
            node = str(row.get("provider_id") or "unknown")
            errors_by_node[node] = errors_by_node.get(node, 0) + 1

    incidents = []
    for node in network_map.get("nodes") or []:
        node_id = str(node.get("id") or "unknown")
        if not node.get("online"):
            incidents.append(_incident(node_id, "node_offline", "critical", "quarantine", "AssistX projection marks node offline"))
        elif not node.get("report_fresh"):
            incidents.append(_incident(node_id, "stale_report", "warning", "observe", "node self-report is stale or missing"))
        elif not node.get("loaded_models"):
            incidents.append(_incident(node_id, "no_loaded_models", "warning", "drain", "online node reports no loaded models"))
        if errors_by_node.get(node_id, 0) >= 3:
            incidents.append(_incident(node_id, "repeated_runtime_failures", "critical", "quarantine", f"{errors_by_node[node_id]} recent runtime failures"))
        for value in values_by_node.get(node_id, []):
            if int(value.get("sample_count") or 0) >= 3 and float(value.get("success_rate") or 0) < 0.7:
                incidents.append(_incident(
                    node_id,
                    "model_quality_degraded",
                    "warning",
                    "drain_model",
                    f"{value.get('model_id')} success rate is {float(value.get('success_rate') or 0):.0%}",
                    model_id=value.get("model_id"),
                ))

    incidents.sort(key=lambda row: ({"critical": 0, "warning": 1, "info": 2}.get(row["severity"], 9), row["node_id"], row["incident_type"]))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "automatic_detection": True,
        "automatic_quarantine": False,
        "summary": {
            "incidents": len(incidents),
            "critical": sum(row["severity"] == "critical" for row in incidents),
            "warning": sum(row["severity"] == "warning" for row in incidents),
            "affected_nodes": len({row["node_id"] for row in incidents}),
        },
        "incidents": incidents,
        "recovery_sequence": ["observe", "quarantine", "drain", "operator_restart", "verify", "rejoin"],
    }


def _incident(
    node_id: str,
    incident_type: str,
    severity: str,
    recommended_action: str,
    detail: str,
    *,
    model_id: object = None,
) -> dict[str, Any]:
    return {
        "incident_key": f"{node_id}:{incident_type}:{model_id or 'node'}",
        "node_id": node_id,
        "model_id": model_id,
        "incident_type": incident_type,
        "severity": severity,
        "recommended_action": recommended_action,
        "detail": detail,
    }
