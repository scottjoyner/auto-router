from auto_router.tool_call_reliability import (
    REQUIRED_TOOL_CALL_PROBES,
    build_tool_use_probe_evidence,
    grade_tool_call_probe,
    tool_use_evidence_passed,
)


def _ok(probe_id: str) -> dict:
    return {
        "probe_id": probe_id,
        "expected_tools": ["read_file"],
        "tool_calls": [{"name": "read_file", "arguments": '{"path":"README.md"}'}],
        "raw_output": "",
    }


def test_expected_tool_probe_fails_when_model_emits_no_call() -> None:
    result = grade_tool_call_probe({
        "probe_id": "wrong_tool_selection",
        "expected_tools": ["read_file"],
        "tool_calls": [],
    })
    assert result["passed"] is False
    assert result["failures"] == ["missing_tool_call"]


def test_wrong_tool_selection_is_visible() -> None:
    result = grade_tool_call_probe({
        "probe_id": "wrong_tool_selection",
        "expected_tools": ["read_file"],
        "tool_calls": [{"name": "terminal", "arguments": '{"command":"cat README.md"}'}],
    })
    assert result["passed"] is False
    assert result["failures"] == ["wrong_tool_selection"]


def test_mixed_serialization_duplicate_and_malformed_calls_are_visible() -> None:
    mixed = grade_tool_call_probe({
        "probe_id": "mixed_serialization",
        "expected_tools": ["read_file"],
        "tool_calls": [{"name": "read_file", "arguments": '{"path":"README.md"}'}],
        "raw_output": '<tool_call name="read_file">{"path":"README.md"}</tool_call>',
    })
    assert "mixed_serialization" in mixed["failures"]

    duplicate = grade_tool_call_probe({
        "probe_id": "duplicate_semantic_call",
        "expected_tools": ["read_file"],
        "tool_calls": [
            {"name": "read_file", "arguments": '{"path":"README.md"}'},
            {"name": "read_file", "arguments": '{ "path" : "README.md" }'},
        ],
    })
    assert "duplicate_semantic_call" in duplicate["failures"]

    malformed = grade_tool_call_probe({
        "probe_id": "malformed_arguments",
        "expected_tools": ["read_file"],
        "tool_calls": [{"name": "read_file", "arguments": '{"path":'}],
    })
    assert "malformed_arguments" in malformed["failures"]


def test_hidden_info_probe_rejects_filesystem_spelunking() -> None:
    result = grade_tool_call_probe({
        "probe_id": "hidden_info_spelunking",
        "expects_no_tool": True,
        "forbidden_tools": ["terminal", "grep", "read_file", "search_files"],
        "tool_calls": [{"name": "terminal", "arguments": '{"command":"grep -n secret config.yaml"}'}],
    })
    assert result["passed"] is False
    assert set(result["failures"]) == {"unexpected_tool_call", "forbidden_tool_selection"}


def test_all_five_probes_are_required_for_tool_use_evidence() -> None:
    observations = [_ok(probe) for probe in REQUIRED_TOOL_CALL_PROBES]
    observations[-1] = {
        "probe_id": "hidden_info_spelunking",
        "expects_no_tool": True,
        "forbidden_tools": ["terminal", "read_file"],
        "tool_calls": [],
    }
    probe = build_tool_use_probe_evidence(observations)
    score = {"quality_floor_passed": True, "tool_call_probe": probe}
    assert probe["passed"] is True
    assert tool_use_evidence_passed(score) is True

    missing = build_tool_use_probe_evidence(observations[:-1])
    assert missing["passed"] is False
    assert tool_use_evidence_passed({"quality_floor_passed": True, "tool_call_probe": missing}) is False


def test_quality_floor_failure_cannot_be_laundered_by_probe_pass() -> None:
    observations = [_ok(probe) for probe in REQUIRED_TOOL_CALL_PROBES]
    observations[-1] = {"probe_id": "hidden_info_spelunking", "expects_no_tool": True, "tool_calls": []}
    probe = build_tool_use_probe_evidence(observations)
    assert tool_use_evidence_passed({"quality_floor_passed": False, "tool_call_probe": probe}) is False
