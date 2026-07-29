from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from auto_router.quality_evidence import TASK_FAMILIES


def build_benchmark_plan(
    value_matrix: dict[str, Any],
    quality_evidence: dict[str, Any],
    *,
    stale_days: int = 14,
) -> dict[str, Any]:
    """Create non-mutating benchmark demand for currently loaded models."""
    evidence = {
        (str(row["node_id"]), str(row["model_id"]), str(row["task_family"])): row
        for row in quality_evidence.get("entries") or []
    }
    cutoff = datetime.now(UTC) - timedelta(days=max(1, stale_days))
    requests: list[dict[str, Any]] = []
    for entry in value_matrix.get("entries") or []:
        if not entry.get("loaded") or not entry.get("online"):
            continue
        node = str(entry["node_id"])
        model = str(entry["model_id"])
        for family in TASK_FAMILIES:
            row = evidence.get((node, model, family))
            reasons: list[str] = []
            priority = 0
            if row is None:
                reasons.append("missing task-family evidence")
                priority += 50
            else:
                if float(row.get("confidence") or 0) < 0.5:
                    reasons.append("low quality confidence")
                    priority += 30
                observed = _parse_time(row.get("last_observed_at"))
                if observed is None or observed < cutoff:
                    reasons.append("quality evidence is stale")
                    priority += 20
            if int(entry.get("sample_count") or 0) < 3:
                reasons.append("insufficient throughput evidence")
                priority += 25
            if not reasons:
                continue
            requests.append({
                "benchmark_id": f"{node}:{model}:{family}",
                "node_id": node,
                "model_id": model,
                "task_family": family,
                "priority": priority,
                "reasons": reasons,
                "execution_mode": "dry_run",
                "requires_model_load": False,
                "requires_assistx_claim": True,
                "estimated_prompts": 3,
            })
    requests.sort(key=lambda row: (-int(row["priority"]), row["node_id"], row["model_id"], row["task_family"]))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "advisory_only": True,
        "auto_load_allowed": False,
        "summary": {
            "requests": len(requests),
            "nodes": len({row["node_id"] for row in requests}),
            "models": len({(row["node_id"], row["model_id"]) for row in requests}),
        },
        "requests": requests,
    }


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None
