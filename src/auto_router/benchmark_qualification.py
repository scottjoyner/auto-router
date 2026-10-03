"""Benchmark-only qualification evidence and advisory role derivation.

This module is a pure vocabulary + pure-function layer over plain dicts. It
observes nothing, imports no admission/projection/policy authority, mutates no
configuration, and exposes no HTTP surface. Nothing produced here can make a
node or a model routable: ``build_qualification_report`` always reports
``creates_provider_eligibility: False`` and ``signed_admission_required: True``.

The single organizing idea is that *capability* and *throughput* are separate
axes. Raw tokens/sec is recorded as an advisory ordering hint only; it is
structurally incapable of overriding a quality floor because it never appears
in :data:`QUALITY_FLOORS`.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

# --- Closed vocabulary ------------------------------------------------------

QUALIFICATION_LEVELS: tuple[str, ...] = (
    "L0_PROTOCOL",
    "L1_EXACT_GROUNDING",
    "L2_SOURCE_GROUNDING",
    "L3_DIAGNOSIS",
    "L4_PATCH",
)
LEVEL_ORDER: dict[str, int] = {level: index for index, level in enumerate(QUALIFICATION_LEVELS)}

LEVEL_INTENT: dict[str, str] = {
    "L0_PROTOCOL": "deterministic tiny response proves the wire protocol works",
    "L1_EXACT_GROUNDING": "answer one exact fact from the supplied context",
    "L2_SOURCE_GROUNDING": "name exact symbols and relationships from a bounded source slice",
    "L3_DIAGNOSIS": "identify root cause and minimal repair for a known bug slice",
    "L4_PATCH": "propose a patch evaluated inside an isolated temporary worktree",
}

# Seven distinct axes. They are deliberately not collapsed: a model can be
# listed but unloadable, loadable but protocol-broken, protocol-clean but
# ungrounded, grounded but unable to patch.
CAPABILITIES: tuple[str, ...] = (
    "endpoint_reachable",
    "model_visible",
    "model_loaded",
    "protocol_usable",
    "grounding_usable",
    "coding_usable",
    "review_usable",
)

# No generic "model_failed" bucket exists. Every observation that did not pass is
# attributed to exactly one named cause so remediation is decidable.
FAILURE_MODES: tuple[str, ...] = (
    "empty_usable_content",
    "reasoning_only_output",
    "length_truncated",
    "timeout",
    "context_not_grounded",
    "wrong_response_field",
    "invalid_patch",
    "test_failure",
)

QUALIFICATION_ROLES: tuple[str, ...] = (
    "CODE_QUALIFIED",
    "REVIEW_QUALIFIED",
    "SCOUT_QUALIFIED",
    "SUMMARY_ONLY",
    "UNQUALIFIED",
)

# Quality floors only. tokens_per_second and time_to_first_token_ms are absent by
# construction: throughput is never a qualification input.
QUALITY_FLOORS: dict[str, dict[str, Any]] = {
    "CODE_QUALIFIED": {
        "min_level": "L4_PATCH",
        "min_pass_rate": 0.8,
        "min_grounding_accuracy": 0.8,
        "min_confidence": 0.6,
    },
    "REVIEW_QUALIFIED": {
        "min_level": "L3_DIAGNOSIS",
        "min_pass_rate": 0.7,
        "min_grounding_accuracy": 0.6,
        "min_confidence": 0.5,
    },
    "SCOUT_QUALIFIED": {
        "min_level": "L1_EXACT_GROUNDING",
        "min_pass_rate": 0.5,
        "min_grounding_accuracy": 0.5,
        "min_confidence": 0.4,
    },
    "SUMMARY_ONLY": {
        "min_level": "L1_EXACT_GROUNDING",
        "min_pass_rate": 0.0,
        "min_grounding_accuracy": 0.0,
        "min_confidence": 0.2,
    },
    "UNQUALIFIED": {
        "min_level": None,
        "min_pass_rate": None,
        "min_grounding_accuracy": None,
        "min_confidence": None,
    },
}

CONFIDENCE_FLOOR = 0.5

# Failure modes that invalidate a specific role's own deliverable. A model can
# legitimately fail L3 and still be a good scout, so blocking is per-role and
# never blanket.
BLOCKING_FAILURE_MODES: dict[str, tuple[str, ...]] = {
    "CODE_QUALIFIED": ("invalid_patch", "test_failure", "empty_usable_content", "timeout"),
    "REVIEW_QUALIFIED": ("empty_usable_content", "timeout"),
    "SCOUT_QUALIFIED": (),
    "SUMMARY_ONLY": (),
}

EVIDENCE_SCHEMA_FIELDS: tuple[str, ...] = (
    "node_id",
    "task_family",
    "runtime_instance_id",
    "runtime_kind",
    "runtime_version",
    "model_instance_id",
    "provider_model",
    "alias",
    "quantization",
    "context_length",
    "test_level",
    "prompt_hash",
    "source_hash",
    "succeeded",
    "failure_mode",
    "grounding_correct",
    "patch_applied",
    "patch_valid",
    "tests_passed",
    "time_to_first_token_ms",
    "tokens_per_second",
    "finish_reason",
    "observed_at",
    "confidence",
)

AUXILIARY_OBSERVATION_FIELDS: tuple[str, ...] = (
    "endpoint_reached",
    "model_visible",
    "model_loaded",
    "error_type",
    "usable_content_chars",
    "reasoning_chars",
)

REJECTED_SENTINEL = "REJECTED"

_ADVISORY_GUARD = {
    "advisory_only": True,
    "auto_load_allowed": False,
    "mode": "simulation",
    "executable": False,
    "mutates_model_config": False,
    "creates_provider_eligibility": False,
    "signed_admission_required": True,
}

BOUNDARY_STATEMENT = (
    "Benchmark evidence is advisory. It records what was observed at a rung of the "
    "qualification ladder and can only ever propose a role. It cannot grant provider "
    "eligibility, routing roles, worker mode, code-execution permission, or admission; "
    "those remain the exclusive province of signed admission."
)

_LENGTH_FINISH_REASONS = ("length", "max_tokens", "max_output_tokens", "truncated")
_TIMEOUT_ERROR_HINTS = ("timeout", "timed_out", "deadline", "canceled", "cancelled")


# --- Normalization -----------------------------------------------------------


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "ok", "pass", "passed"}:
            return True
        if text in {"false", "no", "0", "fail", "failed"}:
            return False
    return None


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def normalize_evidence(row: object) -> dict[str, Any]:
    """Fill defaults for one observation and reject malformed input.

    Hard rejections (raise ``ValueError``): not a mapping, no ``node_id``, no
    model identity, a ``test_level`` outside :data:`QUALIFICATION_LEVELS`, or an
    explicit ``failure_mode`` outside :data:`FAILURE_MODES`. Soft problems are
    repaired and reported in the ``rejections`` list instead of being trusted.
    """
    if not isinstance(row, dict):
        raise ValueError("evidence row must be a mapping")
    node = _text(row.get("node_id"))
    if not node:
        raise ValueError("evidence row requires node_id")
    provider_model = _text(row.get("provider_model"))
    alias = _text(row.get("alias"))
    if not provider_model and not alias:
        raise ValueError("evidence row requires provider_model or alias")

    rejections: list[str] = []
    level = str(row.get("test_level") or "").strip().upper()
    if not level:
        level = "L0_PROTOCOL"
        rejections.append("missing test_level defaulted to L0_PROTOCOL")
    if level not in LEVEL_ORDER:
        raise ValueError(f"unknown test_level: {level!r}")

    declared_failure = _text(row.get("failure_mode"))
    if declared_failure and declared_failure not in FAILURE_MODES:
        raise ValueError(f"unknown failure_mode: {declared_failure!r}")

    confidence = max(0.0, min(1.0, _as_float(row.get("confidence"), 0.0)))
    if "confidence" in row and not isinstance(row.get("confidence"), (int, float)):
        rejections.append("non-numeric confidence coerced")

    ttft = _as_optional_float(row.get("time_to_first_token_ms"))
    if ttft is not None and ttft < 0:
        ttft = None
        rejections.append("negative time_to_first_token_ms dropped")
    tps = _as_optional_float(row.get("tokens_per_second"))
    if tps is not None and tps < 0:
        tps = None
        rejections.append("negative tokens_per_second dropped")

    usable_chars = _as_int(row.get("usable_content_chars"), 0)
    reasoning_chars = _as_int(row.get("reasoning_chars"), 0)
    succeeded = bool(row.get("succeeded"))
    endpoint_reached = _as_optional_bool(row.get("endpoint_reached"))
    if endpoint_reached is None:
        endpoint_reached = succeeded or ttft is not None
    model_visible = _as_optional_bool(row.get("model_visible"))
    if model_visible is None:
        model_visible = True
    model_loaded = _as_optional_bool(row.get("model_loaded"))
    if model_loaded is None:
        model_loaded = succeeded

    record: dict[str, Any] = {
        "node_id": node,
        "task_family": str(row.get("task_family") or "general").strip().lower() or "general",
        "runtime_instance_id": _text(row.get("runtime_instance_id")),
        "runtime_kind": _text(row.get("runtime_kind")),
        "runtime_version": _text(row.get("runtime_version")),
        "model_instance_id": _text(row.get("model_instance_id")),
        "provider_model": provider_model,
        "alias": alias,
        "quantization": _text(row.get("quantization")),
        "context_length": _as_int(row.get("context_length"), 0) or None,
        "test_level": level,
        "prompt_hash": _text(row.get("prompt_hash")),
        "source_hash": _text(row.get("source_hash")),
        "succeeded": succeeded,
        "failure_mode": declared_failure,
        "grounding_correct": _as_optional_bool(row.get("grounding_correct")),
        "patch_applied": _as_optional_bool(row.get("patch_applied")),
        "patch_valid": _as_optional_bool(row.get("patch_valid")),
        "tests_passed": _as_optional_bool(row.get("tests_passed")),
        "time_to_first_token_ms": ttft,
        "tokens_per_second": tps,
        "finish_reason": _text(row.get("finish_reason")),
        "observed_at": _observed_at(row.get("observed_at")),
        "confidence": round(confidence, 3),
        "endpoint_reached": endpoint_reached,
        "model_visible": model_visible,
        "model_loaded": model_loaded,
        "error_type": _text(row.get("error_type")),
        "usable_content_chars": usable_chars,
        "reasoning_chars": reasoning_chars,
        "rejections": rejections,
    }
    if not record["prompt_hash"]:
        rejections.append("missing prompt_hash: raw prompt text is never stored")
    return record


def _observed_at(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return datetime.now(UTC).isoformat()


def normalize_evidence_rows(rows: object) -> list[dict[str, Any]]:
    """Normalize many rows, dropping hard rejections instead of raising."""
    normalized: list[dict[str, Any]] = []
    for row in rows or []:
        try:
            normalized.append(normalize_evidence(row))
        except ValueError:
            continue
    return normalized


# --- Failure taxonomy --------------------------------------------------------


def classify_failure(row: dict[str, Any]) -> str | None:
    """Name the cause of a non-passing observation.

    Returns one member of :data:`FAILURE_MODES`, or ``None`` when the row
    passed. Ordering is deterministic and never returns a generic bucket: an
    unrecognised shape still resolves to ``empty_usable_content``.
    """
    if not isinstance(row, dict):
        raise ValueError("evidence row must be a mapping")
    declared = _text(row.get("failure_mode"))
    if declared:
        return declared
    if row.get("succeeded"):
        return None

    error_type = (_text(row.get("error_type")) or "").lower()
    if any(hint in error_type for hint in _TIMEOUT_ERROR_HINTS):
        return "timeout"

    finish_reason = (_text(row.get("finish_reason")) or "").lower()
    if any(hint in finish_reason for hint in _LENGTH_FINISH_REASONS):
        return "length_truncated"

    usable = _as_int(row.get("usable_content_chars"), 0)
    reasoning = _as_int(row.get("reasoning_chars"), 0)
    if usable <= 0 and reasoning > 0:
        return "reasoning_only_output"
    if usable <= 0:
        return "empty_usable_content"

    if _as_optional_bool(row.get("patch_valid")) is False:
        return "invalid_patch"
    if _as_optional_bool(row.get("tests_passed")) is False:
        return "test_failure"
    if _as_optional_bool(row.get("grounding_correct")) is False:
        return "context_not_grounded"
    if _as_optional_bool(row.get("wrong_response_field")) is False:
        return "wrong_response_field"
    if row.get("wrong_response_field") is True:
        return "wrong_response_field"
    return "context_not_grounded"


def annotate_failure(row: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of ``row`` with its ``failure_mode`` resolved."""
    resolved = dict(row)
    resolved["failure_mode"] = classify_failure(row)
    resolved["succeeded"] = resolved["failure_mode"] is None
    return resolved


def failure_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in rows:
        mode = classify_failure(row)
        if mode:
            counts[mode] = counts.get(mode, 0) + 1
    return {
        "taxonomy": list(FAILURE_MODES),
        "generic_buckets": [],
        "counts": dict(sorted(counts.items())),
        "unattributed": sum(counts.values()),
    }


# --- Capability ladder -------------------------------------------------------


def derive_capabilities(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate the seven capability axes independently for one model."""
    observed = [row for row in rows if isinstance(row, dict)]
    passed = [row for row in observed if row.get("succeeded")]
    highest = _highest_level(passed)
    levels_passed = _implied_levels(highest)
    level_passed = {level: level in levels_passed for level in QUALIFICATION_LEVELS}
    grounding_graded = [
        row for row in observed if _as_optional_bool(row.get("grounding_correct")) is not None
    ]
    grounded = [row for row in grounding_graded if row.get("grounding_correct") is True]
    patch_rows = [row for row in observed if str(row.get("test_level")) == "L4_PATCH"]

    endpoint_reachable = any(row.get("endpoint_reached") is True for row in observed)
    model_visible = any(row.get("model_visible") is True for row in observed)
    model_loaded = any(row.get("model_loaded") is True for row in observed)
    protocol_usable = level_passed["L0_PROTOCOL"]
    grounding_usable = bool(
        model_loaded
        and level_passed["L1_EXACT_GROUNDING"]
        and grounded
    )
    review_usable = bool(
        model_loaded
        and level_passed["L3_DIAGNOSIS"]
        and any(
            row.get("succeeded") and row.get("grounding_correct") is True for row in observed
        )
    )
    coding_usable = bool(
        review_usable
        and level_passed["L4_PATCH"]
        and any(
            row.get("patch_valid") is True and row.get("tests_passed") is True
            for row in patch_rows
        )
    )

    return {
        "endpoint_reachable": endpoint_reachable,
        "model_visible": model_visible,
        "model_loaded": model_loaded,
        "protocol_usable": protocol_usable,
        "grounding_usable": grounding_usable,
        "coding_usable": coding_usable,
        "review_usable": review_usable,
        "levels_passed": levels_passed,
        "highest_level_passed": highest,
        "samples": len(observed),
        "samples_passed": len(passed),
        "basis": {
            "endpoint_reachable": "any observation recorded a live transport",
            "model_visible": "model appeared in the harness inventory",
            "model_loaded": "any observation required a resident model",
            "protocol_usable": "L0_PROTOCOL produced a deterministic response",
            "grounding_usable": "L1_EXACT_GROUNDING answered from supplied context",
            "review_usable": "L3_DIAGNOSIS identified a root cause from bounded source",
            "coding_usable": "L4_PATCH produced a patch that applied and passed tests",
        },
        "axes_are_distinct": True,
        "collapsible": False,
    }


# --- Role derivation ---------------------------------------------------------


def _highest_level(passed: list[dict[str, Any]]) -> str | None:
    best: str | None = None
    for row in passed:
        level = str(row.get("test_level") or "")
        if level not in LEVEL_ORDER:
            continue
        if best is None or LEVEL_ORDER[level] > LEVEL_ORDER[best]:
            best = level
    return best


def _implied_levels(highest: str | None) -> list[str]:
    """A pass at rung N implies every lower rung: the ladder is nested."""
    if highest is None:
        return []
    cutoff = LEVEL_ORDER[highest]
    return [level for level in QUALIFICATION_LEVELS if LEVEL_ORDER[level] <= cutoff]


def evaluate_role_floor(
    role: str,
    stats: dict[str, Any],
    *,
    confidence_floor: float = CONFIDENCE_FLOOR,
) -> dict[str, Any]:
    """Evaluate one quality floor. Throughput is not an input by construction."""
    floor = QUALITY_FLOORS[role]
    reasons: list[str] = []
    min_level = floor["min_level"]
    highest = stats.get("highest_level_passed")
    if min_level is not None:
        reached = highest is not None and LEVEL_ORDER[str(highest)] >= LEVEL_ORDER[str(min_level)]
        if not reached:
            reasons.append(
                f"highest passed level {highest or 'none'} is below required {min_level}"
            )
    if floor["min_pass_rate"] is not None and float(stats.get("pass_rate") or 0.0) < float(
        floor["min_pass_rate"]
    ):
        reasons.append(
            f"pass_rate {round(float(stats.get('pass_rate') or 0.0), 3)} below "
            f"{floor['min_pass_rate']}"
        )
    if floor["min_grounding_accuracy"] is not None and float(
        stats.get("grounding_accuracy") or 0.0
    ) < float(floor["min_grounding_accuracy"]):
        reasons.append(
            f"grounding accuracy {round(float(stats.get('grounding_accuracy') or 0.0), 3)} "
            f"below {floor['min_grounding_accuracy']}"
        )
    if float(stats.get("confidence") or 0.0) < max(confidence_floor, float(
        floor["min_confidence"] or 0.0
    )):
        reasons.append(
            f"confidence {round(float(stats.get('confidence') or 0.0), 3)} below "
            f"{max(confidence_floor, float(floor['min_confidence'] or 0.0))}"
        )
    hard_failures = stats.get("hard_failure_modes") or []
    blocking = set(BLOCKING_FAILURE_MODES.get(role, ()))
    observed_blocking = sorted({mode for mode in hard_failures if mode in blocking})
    if observed_blocking:
        reasons.append(f"failure modes blocking this role: {', '.join(observed_blocking)}")
    return {
        "role": role,
        "passed": not reasons,
        "floor": dict(floor),
        "reasons": reasons,
        "throughput_considered": False,
    }


def evidence_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate normalized rows into the statistics the floors read."""
    observed = [row for row in rows if isinstance(row, dict)]
    annotated = [annotate_failure(row) for row in observed]
    passed = [row for row in annotated if row["succeeded"]]
    graded = [row for row in annotated if row.get("grounding_correct") is not None]
    grounded = [row for row in graded if row.get("grounding_correct") is True]
    confidences = [float(row.get("confidence") or 0.0) for row in annotated] or [0.0]
    ttfts = [
        float(row["time_to_first_token_ms"])
        for row in annotated
        if row.get("time_to_first_token_ms") is not None
    ]
    tpss = [
        float(row["tokens_per_second"])
        for row in annotated
        if row.get("tokens_per_second") is not None
    ]
    return {
        "samples": len(annotated),
        "samples_passed": len(passed),
        "pass_rate": round(len(passed) / len(annotated), 3) if annotated else 0.0,
        "highest_level_passed": _highest_level(passed),
        "grounding_samples": len(graded),
        "grounding_accuracy": round(len(grounded) / len(graded), 3) if graded else 0.0,
        "confidence": round(max(confidences), 3),
        "hard_failure_modes": [
            row["failure_mode"] for row in annotated if row.get("failure_mode") in FAILURE_MODES
        ],
        "advisory_throughput": {
            "median_time_to_first_token_ms": round(sorted(ttfts)[len(ttfts) // 2], 3)
            if ttfts
            else None,
            "median_tokens_per_second": round(sorted(tpss)[len(tpss) // 2], 3) if tpss else None,
            "note": "advisory only; never read by evaluate_role_floor",
        },
    }


def derive_role(
    rows: list[dict[str, Any]],
    *,
    confidence_floor: float = CONFIDENCE_FLOOR,
) -> dict[str, Any]:
    """Derive exactly one advisory role from evidence for one model+family.

    Floors are checked from the strictest (``CODE_QUALIFIED``) to the most
    permissive, and the first satisfied floor wins. ``UNQUALIFIED`` is the
    fallback and is always returned when nothing else is earned.
    """
    stats = evidence_stats(rows)
    evaluated = [
        evaluate_role_floor(role, stats, confidence_floor=confidence_floor)
        for role in QUALIFICATION_ROLES[:-1]
    ]
    role = "UNQUALIFIED"
    for candidate in evaluated:
        if candidate["passed"]:
            role = str(candidate["role"])
            break
    failures = stats["hard_failure_modes"]
    return {
        "role": role,
        "qualified_for_coding": role == "CODE_QUALIFIED",
        "qualified_for_review": role in {"CODE_QUALIFIED", "REVIEW_QUALIFIED"},
        "qualified_for_scouting": role in {
            "CODE_QUALIFIED",
            "REVIEW_QUALIFIED",
            "SCOUT_QUALIFIED",
        },
        "qualified_for_summary_only": role != "UNQUALIFIED",
        "stats": stats,
        "floors": evaluated,
        "blocking_failure_modes": sorted(set(failures)),
        "throughput_cannot_override_quality_floor": True,
        "confidence_floor": confidence_floor,
        **_ADVISORY_GUARD,
    }


# --- Report ------------------------------------------------------------------


def ladder_summary() -> dict[str, Any]:
    return {
        "levels": [
            {"level": level, "index": LEVEL_ORDER[level], "intent": LEVEL_INTENT[level]}
            for level in QUALIFICATION_LEVELS
        ],
        "capabilities": list(CAPABILITIES),
        "failure_modes": list(FAILURE_MODES),
        "roles": list(QUALIFICATION_ROLES),
        "quality_floors": {role: dict(floor) for role, floor in QUALITY_FLOORS.items()},
        "throughput_in_quality_floors": False,
    }


def build_qualification_report(
    rows: object,
    *,
    confidence_floor: float = CONFIDENCE_FLOOR,
) -> dict[str, Any]:
    """Aggregate normalized evidence into an advisory qualification report."""
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    rejected = 0
    for row in rows or []:
        try:
            record = normalize_evidence(row)
        except ValueError:
            rejected += 1
            continue
        key = (
            record["node_id"],
            record["provider_model"] or record["alias"] or "",
            record["task_family"],
        )
        grouped[key].append(record)

    entries: list[dict[str, Any]] = []
    for (node, model, family), group in grouped.items():
        capabilities = derive_capabilities(group)
        role = derive_role(group, confidence_floor=confidence_floor)
        entries.append({
            "node_id": node,
            "model_id": model,
            "task_family": family,
            "runtime_instance_id": group[0]["runtime_instance_id"],
            "runtime_kind": group[0]["runtime_kind"],
            "runtime_version": group[0]["runtime_version"],
            "model_instance_id": group[0]["model_instance_id"],
            "alias": group[0]["alias"],
            "quantization": group[0]["quantization"],
            "context_length": group[0]["context_length"],
            "highest_level_passed": capabilities["highest_level_passed"],
            "levels_passed": capabilities["levels_passed"],
            "capabilities": capabilities,
            "failures": failure_summary(group),
            "role": role["role"],
            "qualified_for_coding": role["qualified_for_coding"],
            "qualified_for_review": role["qualified_for_review"],
            "qualified_for_scouting": role["qualified_for_scouting"],
            "role_floors": role["floors"],
            "role_stats": role["stats"],
            "advisory_only": True,
            "auto_load_allowed": False,
            "executable": False,
        })

    entries.sort(key=lambda row: (row["node_id"], row["model_id"], row["task_family"]))
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["role"]] = counts.get(entry["role"], 0) + 1
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "ladder": ladder_summary(),
        "schema": {
            "required_fields": list(EVIDENCE_SCHEMA_FIELDS),
            "auxiliary_observation_fields": list(AUXILIARY_OBSERVATION_FIELDS),
            "stores_raw_prompt_text": False,
        },
        "summary": {
            "entries": len(entries),
            "rejected_rows": rejected,
            "roles": dict(sorted(counts.items())),
            "models": len({(row["node_id"], row["model_id"]) for row in entries}),
        },
        "boundary": BOUNDARY_STATEMENT,
        "boundary_assertions": {
            "creates_provider_eligibility": False,
            "signed_admission_required": True,
            "grants_routing_roles": False,
            "grants_worker_mode": False,
            "grants_code_execution": False,
            "may_mutate_model_config": False,
            "imports_admission_authority": False,
        },
        **_ADVISORY_GUARD,
        "entries": entries,
    }


def apply_qualification_to_benchmark_plan(
    plan: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Annotate an existing benchmark plan with qualification outcomes.

    The plan is left structurally intact: no request is added, removed,
    reordered, or promoted, and the plan's own ``advisory_only`` and
    ``auto_load_allowed`` flags are preserved. This only attaches the roles so a
    planner or dashboard can see which requests point at models that actually
    earned the lane.

    It deliberately does not touch provider eligibility, admission, or routing:
    benchmark evidence must never be able to make an unadmitted node routable.
    """

    roles = {
        (str(entry["node_id"]), str(entry["model_id"])): entry
        for entry in report.get("entries", [])
        if isinstance(entry, dict)
    }

    annotated = dict(plan)
    requests: list[dict[str, Any]] = []
    for request in plan.get("requests", []):
        if not isinstance(request, dict):
            requests.append(request)
            continue
        entry = roles.get(
            (str(request.get("node_id", "")), str(request.get("model_id", "")))
        )
        updated = dict(request)
        if entry is not None:
            updated["benchmark_role"] = entry.get("role")
            updated["benchmark_role_confidence"] = entry.get("role_stats", {}).get(
                "confidence"
            )
        requests.append(updated)

    annotated["requests"] = requests
    annotated["advisory_only"] = True
    annotated["auto_load_allowed"] = False
    annotated["creates_provider_eligibility"] = False
    annotated["signed_admission_required"] = True
    return annotated


def next_benchmark_targets(
    report: dict[str, Any],
    *,
    confidence_floor: float = CONFIDENCE_FLOOR,
) -> list[dict[str, Any]]:
    """Suggest the next rung to measure. Suggests only; never schedules."""
    requests: list[dict[str, Any]] = []
    for entry in report.get("entries") or []:
        highest = entry.get("highest_level_passed")
        index = LEVEL_ORDER.get(str(highest), -1)
        for level in QUALIFICATION_LEVELS:
            if LEVEL_ORDER[level] > index:
                requests.append({
                    "node_id": entry["node_id"],
                    "model_id": entry["model_id"],
                    "task_family": entry["task_family"],
                    "target_level": level,
                    "current_role": entry["role"],
                    "confidence": (entry.get("role_stats") or {}).get("confidence", 0.0),
                    "confidence_floor": confidence_floor,
                    "execution_mode": "dry_run",
                    "requires_model_load": False,
                    "requires_admission": True,
                })
                break
    requests.sort(key=lambda row: (row["node_id"], row["model_id"], row["task_family"]))
    return requests
