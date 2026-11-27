"""R37: duplicate probe_id must not launder a failing tool-call canary.

Hermetic: only stdlib + the pure-Python grader; no network, DB, credentials or fleet I/O.
"""

from auto_router.tool_call_reliability import (
    REQUIRED_TOOL_CALL_PROBES,
    build_tool_use_probe_evidence,
    tool_use_evidence_passed,
)


def _ok(probe_id: str) -> dict:
    return {
        "probe_id": probe_id,
        "expected_tools": ["read_file"],
        "tool_calls": [{"name": "read_file", "arguments": '{"path":"README.md"}'}],
        "raw_output": "",
    }


def _fail(probe_id: str) -> dict:
    """Same probe, but the model emitted no required call."""
    return {"probe_id": probe_id, "expected_tools": ["read_file"], "tool_calls": []}


def _score(probe: dict) -> bool:
    return tool_use_evidence_passed({"quality_floor_passed": True, "tool_call_probe": probe})


def test_later_passing_duplicate_cannot_launder_an_earlier_failure() -> None:
    observations = [_fail("wrong_tool_selection")]
    observations.extend(_ok(probe) for probe in REQUIRED_TOOL_CALL_PROBES)
    probe = build_tool_use_probe_evidence(observations)
    assert probe["passed"] is False
    assert "wrong_tool_selection" in probe["failed_probes"]
    assert "wrong_tool_selection" not in probe["passed_probes"]
    assert _score(probe) is False


def test_earlier_pass_cannot_be_masked_by_a_later_failing_duplicate() -> None:
    """Control: a failing duplicate was already visible before the fix."""
    observations = [_ok(probe) for probe in REQUIRED_TOOL_CALL_PROBES]
    observations.append(_fail("mixed_serialization"))
    probe = build_tool_use_probe_evidence(observations)
    assert probe["passed"] is False
    assert "mixed_serialization" in probe["failed_probes"]
    assert _score(probe) is False


def test_three_way_duplicate_is_still_a_failure() -> None:
    observations = [_ok(probe) for probe in REQUIRED_TOOL_CALL_PROBES]
    observations.extend([_fail("malformed_arguments"), _ok("malformed_arguments")])
    probe = build_tool_use_probe_evidence(observations)
    assert probe["passed"] is False
    assert "malformed_arguments" in probe["failed_probes"]
    assert _score(probe) is False


def test_unique_required_probes_still_pass() -> None:
    """Control: the happy path must keep working after the change."""
    probe = build_tool_use_probe_evidence([_ok(p) for p in REQUIRED_TOOL_CALL_PROBES])
    assert probe["passed"] is True
    assert probe["failed_probes"] == []
    assert _score(probe) is True


def test_missing_probe_still_fails() -> None:
    """Control: the pre-existing missing-probe contract is unchanged."""
    probe = build_tool_use_probe_evidence([_ok(p) for p in REQUIRED_TOOL_CALL_PROBES[:-1]])
    assert probe["passed"] is False
    assert probe["missing_probes"] == [REQUIRED_TOOL_CALL_PROBES[-1]]
    assert _score(probe) is False


def test_rows_without_probe_id_are_not_duplicates() -> None:
    """Control: unnamed rows stay ignored, so they cannot create a false duplicate."""
    observations = [_ok(p) for p in REQUIRED_TOOL_CALL_PROBES]
    observations.extend([_ok(""), _fail("")])
    probe = build_tool_use_probe_evidence(observations)
    assert probe["passed"] is True
    assert probe["failed_probes"] == []
