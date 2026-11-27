"""R42 hermetic regression: non-finite evaluator quality scores must fail closed.

`outcome_quality()` clamps an explicit `metadata.quality_score` into 0..1.  Python's
`min`/`max` do not tame NaN: `max(0.0, min(1.0, nan)) == 1.0`, so a NaN (or +inf)
from a broken or crafted evaluator was admitted as a *perfect* quality observation
even when the outcome itself failed.  That value feeds model ranking and benchmark
qualification, so it must not be admitted.

No network, DB, fleet state, or service calls — stdlib + the module under test only.
"""

from __future__ import annotations

from auto_router.quality_evidence import aggregate_quality, model_quality_index, outcome_quality

INVALID_REASON = "invalid evaluator score (non-finite)"


def _score(value):
    return outcome_quality({"success": False, "metadata": {"quality_score": value}})


def test_nan_float_fails_closed():
    score, reasons = _score(float("nan"))
    assert score == 0.0
    assert INVALID_REASON in reasons
    assert "explicit evaluator score" not in reasons


def test_positive_infinity_fails_closed():
    score, reasons = _score(float("inf"))
    assert score == 0.0
    assert INVALID_REASON in reasons


def test_negative_infinity_fails_closed():
    score, reasons = _score(float("-inf"))
    assert score == 0.0
    assert INVALID_REASON in reasons


def test_nan_string_from_json_fails_closed():
    score, reasons = _score("nan")
    assert score == 0.0
    assert INVALID_REASON in reasons


def test_non_numeric_string_fails_closed_without_raising():
    # Must be a conservative observation, not an exception that breaks the caller.
    score, reasons = _score("not-a-number")
    assert score == 0.0
    assert INVALID_REASON in reasons


def test_failed_outcome_never_becomes_perfect_via_nan():
    score, _ = outcome_quality({
        "success": False,
        "validation_passed": False,
        "metadata": {"quality_score": float("nan")},
    })
    assert score <= 0.35  # validation-failed ceiling, not 1.0


def test_valid_scores_are_still_honored():
    high, reasons = _score(0.95)
    assert high == 0.95
    assert "explicit evaluator score" in reasons
    low, _ = _score(0.0)
    assert low == 0.0


def test_downstream_signals_still_apply_after_rejection():
    score, reasons = outcome_quality({
        "success": False,
        "validation_passed": True,
        "metadata": {"quality_score": float("nan")},
    })
    assert score == 0.8
    assert INVALID_REASON in reasons
    assert "validation passed" in reasons


def test_aggregate_does_not_launder_nan_into_perfect_entry():
    evidence = aggregate_quality([{
        "model": "m",
        "node_id": "x1",
        "success": False,
        "metadata": {"task_family": "code", "quality_score": float("nan")},
    }])
    entry = evidence["entries"][0]
    assert entry["quality_score"] == 0.0
    assert model_quality_index(evidence)[("x1", "m")]["quality_score"] == 0.0
