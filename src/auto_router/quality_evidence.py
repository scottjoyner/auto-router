from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Iterable

TASK_FAMILIES = ("coding", "reasoning", "extraction", "summarization", "tool_use", "long_context")
QUALITY_SAMPLE_TARGET = 8


def normalize_task_family(value: object) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "code": "coding",
        "code_review": "coding",
        "research": "reasoning",
        "analysis": "reasoning",
        "extract": "extraction",
        "summarize": "summarization",
        "summary": "summarization",
        "tools": "tool_use",
        "tool": "tool_use",
        "context": "long_context",
    }
    return aliases.get(text, text if text in TASK_FAMILIES else "general")


def outcome_quality(payload: dict[str, Any]) -> tuple[float, list[str]]:
    """Convert validation metadata into a calibrated 0..1 quality observation."""
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    reasons: list[str] = []
    explicit = metadata.get("quality_score")
    if explicit is not None:
        score = max(0.0, min(1.0, float(explicit)))
        reasons.append("explicit evaluator score")
    else:
        score = 1.0 if payload.get("success") else 0.15
        reasons.append("execution success proxy")
    if payload.get("validation_passed") is False:
        score = min(score, 0.35)
        reasons.append("validation failed")
    elif payload.get("validation_passed") is True:
        score = max(score, 0.8)
        reasons.append("validation passed")
    if metadata.get("user_accepted") is False:
        score = min(score, 0.3)
        reasons.append("user rejected")
    elif metadata.get("user_accepted") is True:
        score = max(score, 0.9)
        reasons.append("user accepted")
    repairs = int(metadata.get("repair_count") or len(payload.get("retry_path") or []))
    if repairs:
        score *= max(0.55, 1.0 - 0.1 * repairs)
        reasons.append(f"{repairs} repair attempts")
    return round(max(0.0, min(1.0, score)), 3), reasons


def aggregate_quality(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for payload in events:
        model = str(payload.get("model") or "").strip()
        if not model:
            continue
        node = str(payload.get("node_id") or payload.get("provider") or "unknown")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        family = normalize_task_family(metadata.get("task_family") or metadata.get("task_kind"))
        score, reasons = outcome_quality(payload)
        groups[(node, model, family)].append({
            "score": score,
            "reasons": reasons,
            "created_at": payload.get("created_at"),
        })

    entries: list[dict[str, Any]] = []
    for (node, model, family), rows in groups.items():
        scores = [float(row["score"]) for row in rows]
        entries.append({
            "node_id": node,
            "model_id": model,
            "task_family": family,
            "quality_score": round(sum(scores) / len(scores), 3),
            "sample_count": len(rows),
            "confidence": round(min(1.0, len(rows) / QUALITY_SAMPLE_TARGET), 3),
            "last_observed_at": max(
                (str(row["created_at"]) for row in rows if row.get("created_at")),
                default=None,
            ),
            "source": "outcome-evaluator",
        })
    entries.sort(key=lambda row: (row["node_id"], row["model_id"], row["task_family"]))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "method": {"sample_target": QUALITY_SAMPLE_TARGET, "metadata_only": True},
        "summary": {
            "events": sum(row["sample_count"] for row in entries),
            "entries": len(entries),
            "models": len({(row["node_id"], row["model_id"]) for row in entries}),
        },
        "entries": entries,
    }


def model_quality_index(quality: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in quality.get("entries") or []:
        grouped[(str(row["node_id"]), str(row["model_id"]))].append(row)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for key, rows in grouped.items():
        weight = sum(int(row["sample_count"]) for row in rows)
        result[key] = {
            "quality_score": round(
                sum(float(row["quality_score"]) * int(row["sample_count"]) for row in rows)
                / max(weight, 1),
                3,
            ),
            "quality_confidence": round(
                sum(float(row["confidence"]) for row in rows) / len(rows), 3
            ),
            "quality_sample_count": weight,
            "task_families": sorted(str(row["task_family"]) for row in rows),
        }
    return result
