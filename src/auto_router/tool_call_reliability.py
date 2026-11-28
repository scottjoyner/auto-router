from __future__ import annotations

import json
from typing import Any, Iterable


TOOL_CALL_PROBE_SCHEMA_VERSION = "1"
REQUIRED_TOOL_CALL_PROBES: tuple[str, ...] = (
    "wrong_tool_selection",
    "mixed_serialization",
    "duplicate_semantic_call",
    "malformed_arguments",
    "hidden_info_spelunking",
)


def _arguments_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _semantic_call_key(call: dict[str, Any]) -> tuple[str, str] | None:
    name = str(call.get("name") or "").strip()
    args = _arguments_object(call.get("arguments"))
    if not name or args is None:
        return None
    return name, json.dumps(args, sort_keys=True, separators=(",", ":"))


def grade_tool_call_probe(observation: dict[str, Any]) -> dict[str, Any]:
    """Grade one tool-call reliability canary without executing any tool.

    The harness supplies only normalized call metadata and raw model text.  The
    grader never receives credentials, filesystem contents, or tool results, so
    it cannot widen execution authority while deciding whether a model is safe
    to *ask* for a tool call.
    """
    probe_id = str(observation.get("probe_id") or "").strip()
    calls = [item for item in observation.get("tool_calls") or [] if isinstance(item, dict)]
    raw_output = str(observation.get("raw_output") or "")
    expected_tools = {str(v) for v in observation.get("expected_tools") or [] if str(v)}
    forbidden_tools = {str(v) for v in observation.get("forbidden_tools") or [] if str(v)}
    expects_no_tool = bool(observation.get("expects_no_tool"))

    failures: list[str] = []
    names = [str(call.get("name") or "").strip() for call in calls]

    if expects_no_tool and calls:
        failures.append("unexpected_tool_call")
    if expected_tools and not calls:
        failures.append("missing_tool_call")
    if expected_tools and any(name not in expected_tools for name in names):
        failures.append("wrong_tool_selection")
    if forbidden_tools.intersection(names):
        failures.append("forbidden_tool_selection")

    raw_lower = raw_output.lower()
    if calls and any(marker in raw_lower for marker in ("<tool_call", "<function_call")):
        failures.append("mixed_serialization")

    seen: set[tuple[str, str]] = set()
    for call in calls:
        key = _semantic_call_key(call)
        if key is None:
            failures.append("malformed_arguments")
            continue
        if key in seen:
            failures.append("duplicate_semantic_call")
        seen.add(key)

    return {
        "probe_id": probe_id,
        "passed": not failures,
        "failures": sorted(set(failures)),
        "tool_call_count": len(calls),
    }


def build_tool_use_probe_evidence(
    observations: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Build the signed-projection evidence block for the five canaries."""
    results = [grade_tool_call_probe(row) for row in observations]
    by_id = {row["probe_id"]: row for row in results if row["probe_id"]}
    missing = [probe for probe in REQUIRED_TOOL_CALL_PROBES if probe not in by_id]
    failed = [
        probe for probe in REQUIRED_TOOL_CALL_PROBES
        if probe in by_id and not by_id[probe]["passed"]
    ]
    passed = [
        probe for probe in REQUIRED_TOOL_CALL_PROBES
        if probe in by_id and by_id[probe]["passed"]
    ]
    return {
        "schema_version": TOOL_CALL_PROBE_SCHEMA_VERSION,
        "passed": not missing and not failed,
        "passed_probes": passed,
        "failed_probes": failed,
        "missing_probes": missing,
        "results": results,
    }


def tool_use_evidence_passed(score: Any) -> bool:
    """True only when signed task-family evidence proves every reliability canary.

    This is intentionally fail-closed.  It can preserve an already-declared
    ``tool_use`` capability, never create one.
    """
    if not isinstance(score, dict) or score.get("quality_floor_passed") is not True:
        return False
    probe = score.get("tool_call_probe")
    if not isinstance(probe, dict):
        return False
    if probe.get("schema_version") != TOOL_CALL_PROBE_SCHEMA_VERSION:
        return False
    if probe.get("passed") is not True:
        return False
    if probe.get("failed_probes") or probe.get("missing_probes"):
        return False
    passed = {str(v) for v in probe.get("passed_probes") or []}
    return set(REQUIRED_TOOL_CALL_PROBES).issubset(passed)
