from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from auto_router.benchmark_qualification import (
    CAPABILITIES,
    FAILURE_MODES,
    QUALIFICATION_LEVELS,
    QUALIFICATION_ROLES,
    REJECTED_SENTINEL,
    apply_qualification_to_benchmark_plan,
    build_qualification_report,
    classify_endpoint_observation,
    classify_failure,
    derive_capabilities,
    derive_role,
    evidence_from_response,
    next_benchmark_targets,
    normalize_evidence,
)

SOURCE = Path(__file__).resolve().parents[1] / "src/auto_router/benchmark_qualification.py"

FORBIDDEN_MODULES = {
    "runtime_projection",
    "runtime_projection_v2",
    "admission",
    "policy",
    "main",
    "main_live",
    "claim_fence",
    "executor_auth",
    "install_benchmark_routing_policy",
    "install_enabled_discovery_policy",
    "discovery_enabled_policy",
    "fleet_routes",
    "models",
    "config",
}


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "node_id": "x1",
        "task_family": "coding",
        "runtime_instance_id": "rt-1",
        "runtime_kind": "llama_cpp",
        "runtime_version": "0.9.2",
        "model_instance_id": "mi-1",
        "provider_model": "qwen-code",
        "alias": "auto/code",
        "quantization": "Q4_K_M",
        "context_length": 32768,
        "test_level": "L4_PATCH",
        "prompt_hash": "sha256:abc",
        "source_hash": "sha256:def",
        "succeeded": True,
        "grounding_correct": True,
        "patch_applied": True,
        "patch_valid": True,
        "tests_passed": True,
        "usable_content_chars": 900,
        "reasoning_chars": 200,
        "time_to_first_token_ms": 310.0,
        "tokens_per_second": 42.0,
        "finish_reason": "stop",
        "observed_at": "2026-10-02T00:00:00+00:00",
        "confidence": 0.9,
    }
    row.update(overrides)
    return row


def _repeat(row: dict[str, object], count: int) -> list[dict[str, object]]:
    return [dict(row) for _ in range(count)]


# --- Named cases -------------------------------------------------------------


def test_fast_but_wrong_is_unqualified_for_coding() -> None:
    rows = _repeat(
        _row(
            test_level="L4_PATCH",
            succeeded=False,
            grounding_correct=False,
            patch_valid=False,
            tests_passed=False,
            tokens_per_second=980.0,
            time_to_first_token_ms=40.0,
            usable_content_chars=120,
        ),
        4,
    )
    role = derive_role(rows)

    assert role["qualified_for_coding"] is False
    assert role["role"] == "UNQUALIFIED"
    assert role["stats"]["highest_level_passed"] is None


def test_slow_but_correct_may_qualify() -> None:
    rows = _repeat(
        _row(
            tokens_per_second=1.5,
            time_to_first_token_ms=9000.0,
        ),
        4,
    )
    role = derive_role(rows)

    assert role["role"] == "CODE_QUALIFIED"
    assert role["qualified_for_coding"] is True


def test_l1_pass_l3_fail_yields_scout_role_only() -> None:
    rows = _repeat(
        _row(
            test_level="L1_EXACT_GROUNDING",
            succeeded=True,
            grounding_correct=True,
            patch_applied=False,
            patch_valid=None,
            tests_passed=None,
        ),
        3,
    ) + _repeat(
        _row(
            test_level="L3_DIAGNOSIS",
            succeeded=False,
            grounding_correct=False,
            patch_applied=False,
            patch_valid=None,
            tests_passed=None,
        ),
        3,
    )
    role = derive_role(rows)

    assert role["stats"]["highest_level_passed"] == "L1_EXACT_GROUNDING"
    assert role["qualified_for_scouting"] is True
    assert role["qualified_for_review"] is False
    assert role["qualified_for_coding"] is False
    assert role["role"] == "SCOUT_QUALIFIED"


def test_timeout_is_classified_explicitly_not_generic() -> None:
    row = _row(
        succeeded=False,
        error_type="read_timeout",
        finish_reason=None,
        usable_content_chars=0,
        reasoning_chars=0,
        tokens_per_second=None,
    )
    mode = classify_failure(row)

    assert mode == "timeout"
    assert mode in FAILURE_MODES
    assert mode != "model_failed"
    assert "model_failed" not in FAILURE_MODES


def test_empty_content_with_reasoning_field_populated_is_reasoning_only() -> None:
    row = _row(
        succeeded=False,
        usable_content_chars=0,
        reasoning_chars=1840,
        finish_reason="stop",
    )

    assert classify_failure(row) == "reasoning_only_output"


def test_quality_floor_failure_cannot_win_from_high_tps() -> None:
    wrong = derive_role(
        _repeat(
            _row(
                succeeded=False,
                grounding_correct=False,
                tokens_per_second=1450.0,
                time_to_first_token_ms=25.0,
            ),
            5,
        )
    )
    correct = derive_role(
        _repeat(_row(tokens_per_second=2.0, time_to_first_token_ms=8000.0), 5)
    )

    assert wrong["stats"]["advisory_throughput"]["median_tokens_per_second"] == 1450.0
    assert correct["stats"]["advisory_throughput"]["median_tokens_per_second"] == 2.0
    assert wrong["role"] == "UNQUALIFIED"
    assert wrong["qualified_for_coding"] is False
    assert correct["qualified_for_coding"] is True
    assert all(floor["throughput_considered"] is False for floor in wrong["floors"])


def test_benchmark_evidence_cannot_create_provider_eligibility() -> None:
    report = build_qualification_report(_repeat(_row(), 4))

    assert report["creates_provider_eligibility"] is False
    assert report["signed_admission_required"] is True
    assert report["advisory_only"] is True
    assert report["auto_load_allowed"] is False
    assert report["mode"] == "simulation"
    assert report["executable"] is False
    assert report["mutates_model_config"] is False
    assert report["boundary_assertions"]["grants_routing_roles"] is False
    assert report["boundary_assertions"]["grants_worker_mode"] is False
    assert report["boundary_assertions"]["grants_code_execution"] is False
    assert report["boundary_assertions"]["may_mutate_model_config"] is False
    assert report["schema"]["stores_raw_prompt_text"] is False
    assert report["entries"][0]["role"] == "CODE_QUALIFIED"

    targets = next_benchmark_targets(report)
    assert all(item["requires_admission"] is True for item in targets)


# --- Static boundary test ----------------------------------------------------


def _imported_module_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                names.add(".")
            elif node.module:
                names.add(node.module.split(".")[0])
    return names


def test_module_imports_none_of_the_forbidden_authority_modules() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imported = _imported_module_names(tree)

    assert not (imported & FORBIDDEN_MODULES)
    assert imported <= {"__future__", "collections", "datetime", "typing"}


def test_module_defines_no_mutating_monkeypatch_or_route_registration() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    banned_attributes = {
        "setattr",
        "monkeypatch",
        "install_benchmark_routing_policy",
        "add_api_route",
        "include_router",
    }
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.Name):
            used.add(node.id)

    assert not (used & banned_attributes)


# --- Vocabulary --------------------------------------------------------------


def test_ladder_and_role_vocabularies_are_closed_and_ordered() -> None:
    assert QUALIFICATION_LEVELS == (
        "L0_PROTOCOL",
        "L1_EXACT_GROUNDING",
        "L2_SOURCE_GROUNDING",
        "L3_DIAGNOSIS",
        "L4_PATCH",
    )
    assert QUALIFICATION_ROLES == (
        "CODE_QUALIFIED",
        "REVIEW_QUALIFIED",
        "SCOUT_QUALIFIED",
        "SUMMARY_ONLY",
        "UNQUALIFIED",
    )
    assert CAPABILITIES == (
        "endpoint_reachable",
        "model_visible",
        "model_loaded",
        "protocol_usable",
        "grounding_usable",
        "coding_usable",
        "review_usable",
    )
    assert len(CAPABILITIES) == 7


def test_failure_taxonomy_distinguishes_the_required_causes() -> None:
    for mode in (
        "empty_usable_content",
        "reasoning_only_output",
        "length_truncated",
        "timeout",
        "context_not_grounded",
        "wrong_response_field",
        "invalid_patch",
        "test_failure",
    ):
        assert mode in FAILURE_MODES
    assert "model_failed" not in FAILURE_MODES


# --- Normalization -----------------------------------------------------------


def test_normalize_fills_defaults_and_keeps_only_hashes() -> None:
    record = normalize_evidence({"node_id": "x1", "provider_model": "m"})

    assert record["test_level"] == "L0_PROTOCOL"
    assert record["quantization"] is None
    assert record["time_to_first_token_ms"] is None
    assert record["tokens_per_second"] is None
    assert record["observed_at"]
    assert record["prompt_hash"] is None
    assert any("prompt_hash" in reason for reason in record["rejections"])
    assert "prompt" not in record
    assert "messages" not in record


def test_normalize_rejects_malformed_rows() -> None:
    for bad in (None, [], "row", 5):
        try:
            normalize_evidence(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")

    for bad in (
        {"provider_model": "m"},
        {"node_id": "", "provider_model": "m"},
        {"node_id": "x1"},
        _row(test_level="L9_TELEPATHY"),
        _row(failure_mode="model_failed"),
    ):
        try:
            normalize_evidence(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def test_normalize_repairs_soft_problems_without_raising() -> None:
    record = normalize_evidence(_row(confidence="oops", tokens_per_second=-4.0))

    assert record["confidence"] == 0.0
    assert record["tokens_per_second"] is None
    assert any("confidence" in reason for reason in record["rejections"])


def test_report_counts_rejected_rows_instead_of_admitting_them() -> None:
    report = build_qualification_report(
        [_row(), {"node_id": "x1"}, {"provider_model": "m"}, _row(test_level="L9_BAD")]
    )

    assert report["summary"]["entries"] == 1
    assert report["summary"]["rejected_rows"] == 3
    assert REJECTED_SENTINEL == "REJECTED"


# --- Taxonomy coverage -------------------------------------------------------


def test_each_failure_cause_is_reachable_from_observation_shapes() -> None:
    assert classify_failure(_row()) is None
    assert classify_failure(_row(succeeded=False, usable_content_chars=0, reasoning_chars=0)) == (
        "empty_usable_content"
    )
    assert classify_failure(
        _row(succeeded=False, usable_content_chars=0, reasoning_chars=99)
    ) == "reasoning_only_output"
    assert classify_failure(
        _row(succeeded=False, usable_content_chars=10, finish_reason="length")
    ) == "length_truncated"
    assert classify_failure(_row(succeeded=False, error_type="deadline_exceeded")) == "timeout"
    assert classify_failure(_row(succeeded=False, grounding_correct=False)) == (
        "context_not_grounded"
    )
    assert classify_failure(_row(succeeded=False, wrong_response_field=True)) == (
        "wrong_response_field"
    )
    assert classify_failure(_row(succeeded=False, patch_valid=False)) == "invalid_patch"
    assert classify_failure(_row(succeeded=False, tests_passed=False)) == "test_failure"


# --- Capability ladder -------------------------------------------------------


def test_capability_axes_are_not_collapsed() -> None:
    visible_not_loaded = derive_capabilities([
        normalize_evidence(_row(endpoint_reached=False, model_loaded=False, succeeded=False))
    ])
    assert visible_not_loaded["model_visible"] is True
    assert visible_not_loaded["model_loaded"] is False
    assert visible_not_loaded["protocol_usable"] is False
    assert visible_not_loaded["grounding_usable"] is False
    assert visible_not_loaded["collapsible"] is False

    full = derive_capabilities([normalize_evidence(_row())])
    assert full["coding_usable"] is True
    assert all(full[axis] is True for axis in CAPABILITIES)


def test_loaded_but_protocol_broken_keeps_grounding_unusable() -> None:
    capabilities = derive_capabilities([
        normalize_evidence(
            _row(
                endpoint_reached=True,
                model_loaded=True,
                succeeded=False,
                test_level="L2_SOURCE_GROUNDING",
                usable_content_chars=0,
                grounding_correct=None,
            )
        )
    ])

    assert capabilities["endpoint_reachable"] is True
    assert capabilities["model_loaded"] is True
    assert capabilities["protocol_usable"] is False
    assert capabilities["grounding_usable"] is False
    assert capabilities["highest_level_passed"] is None


# --- Role rules --------------------------------------------------------------


def test_evidence_below_confidence_floor_confers_no_role() -> None:
    role = derive_role(_repeat(_row(confidence=0.1), 5))

    assert role["role"] == "UNQUALIFIED"
    assert role["qualified_for_coding"] is False
    assert role["qualified_for_summary_only"] is False
    assert any("confidence" in reason for reason in role["floors"][-2]["reasons"])


def test_no_evidence_yields_unqualified() -> None:
    role = derive_role([])

    assert role["role"] == "UNQUALIFIED"
    assert role["stats"]["samples"] == 0
    assert role["stats"]["confidence"] == 0.0


def test_l3_pass_without_l4_yields_review_qualified() -> None:
    role = derive_role(_repeat(_row(test_level="L3_DIAGNOSIS"), 4))

    assert role["role"] == "REVIEW_QUALIFIED"
    assert role["qualified_for_review"] is True
    assert role["qualified_for_coding"] is False


def test_l1_pass_without_grounding_signal_yields_summary_only() -> None:
    role = derive_role(
        _repeat(
            _row(test_level="L1_EXACT_GROUNDING", grounding_correct=None, confidence=0.6),
            2,
        )
    )

    assert role["role"] == "SUMMARY_ONLY"
    assert role["qualified_for_scouting"] is False
    assert role["qualified_for_summary_only"] is True


def test_l1_pass_below_global_confidence_floor_is_unqualified() -> None:
    role = derive_role(_repeat(_row(test_level="L1_EXACT_GROUNDING", confidence=0.25), 3))

    assert role["role"] == "UNQUALIFIED"
    assert role["qualified_for_summary_only"] is False


def test_summary_only_requires_no_grounding_signal() -> None:
    role = derive_role(
        _repeat(
            _row(test_level="L1_EXACT_GROUNDING", grounding_correct=False, confidence=0.6),
            3,
        )
    )

    assert role["role"] == "SUMMARY_ONLY"


def test_blocking_failure_modes_block_coding_role() -> None:
    rows = _repeat(_row(), 4) + _repeat(
        _row(succeeded=False, tests_passed=False, usable_content_chars=40), 1
    )
    role = derive_role(rows)

    assert role["role"] != "CODE_QUALIFIED"
    assert role["qualified_for_coding"] is False
    assert "test_failure" in role["blocking_failure_modes"]


# --- integration boundary: may feed the benchmark planner ----------------
#
# The ticket's integration boundary says benchmark evidence MAY feed the
# benchmark planner, benchmark routing policy, loadout reports, and operator
# dashboards, but MUST NOT make an unadmitted node routable. Annotation is the
# only admissible shape: nothing added, removed, reordered, or promoted.


def _evidence_rows() -> list[dict[str, object]]:
    """A code-qualifying evidence set: two passes at each required rung."""

    rows: list[dict[str, object]] = []
    for level in ("L1_EXACT_GROUNDING", "L3_DIAGNOSIS", "L4_PATCH"):
        rows.extend(
            _repeat(
                _row(
                    test_level=level,
                    succeeded=True,
                    grounding_correct=True,
                    **(
                        {"patch_applied": True, "patch_valid": True, "tests_passed": True}
                        if level == "L4_PATCH"
                        else {}
                    ),
                ),
                2,
            )
        )
    return rows


def _plan() -> dict:
    return {
        "advisory_only": True,
        "auto_load_allowed": False,
        "summary": {"requests": 2},
        "requests": [
            {"node_id": "x1", "model_id": "qwen-code", "priority": 50},
            {"node_id": "destroyer", "model_id": "llama-8b", "priority": 30},
        ],
    }


def test_plan_annotation_attaches_roles_without_structural_change():
    report = build_qualification_report(_evidence_rows())
    before = _plan()

    after = apply_qualification_to_benchmark_plan(before, report)

    assert len(after["requests"]) == len(before["requests"])
    assert [r["node_id"] for r in after["requests"]] == [
        r["node_id"] for r in before["requests"]
    ]
    assert after["summary"] == before["summary"]
    assert after["advisory_only"] is True
    assert after["auto_load_allowed"] is False
    assert after["creates_provider_eligibility"] is False
    annotated = [r for r in after["requests"] if "benchmark_role" in r]
    assert annotated, "a qualifying entry must be attached"
    assert all(r["benchmark_role"] == "CODE_QUALIFIED" for r in annotated)


def test_plan_annotation_does_not_mutate_its_input():
    report = build_qualification_report(_evidence_rows())
    before = _plan()
    snapshot = json.loads(json.dumps(before))

    apply_qualification_to_benchmark_plan(before, report)

    assert before == snapshot, "the planner's own document must not be mutated"


def test_plan_annotation_never_promotes_an_unmeasured_request():
    report = build_qualification_report(_evidence_rows())
    plan = _plan()
    plan["requests"].append({"node_id": "nowhere", "model_id": "ghost"})

    after = apply_qualification_to_benchmark_plan(plan, report)

    ghost = next(r for r in after["requests"] if r["node_id"] == "nowhere")
    assert "benchmark_role" not in ghost
    assert len(after["requests"]) == 3


def test_plan_annotation_survives_an_empty_report():
    after = apply_qualification_to_benchmark_plan(_plan(), {"entries": []})

    assert len(after["requests"]) == 2
    assert all("benchmark_role" not in r for r in after["requests"])


def test_plan_annotation_leaves_every_other_request_field_untouched():
    """Annotation may only ADD role keys.

    Anything else - reprioritising, promoting, reordering, dropping fields - is
    benchmark evidence reaching into planning authority, which the integration
    boundary forbids.
    """

    report = build_qualification_report(_evidence_rows())
    before = _plan()

    after = apply_qualification_to_benchmark_plan(before, report)

    added = {"benchmark_role", "benchmark_role_confidence"}
    for original, updated in zip(before["requests"], after["requests"], strict=True):
        assert {k: v for k, v in updated.items() if k not in added} == original


def test_plan_annotation_forces_advisory_flags_even_if_the_plan_claims_otherwise():
    """A plan that arrives claiming authority must not pass it through."""

    report = build_qualification_report(_evidence_rows())
    hostile = _plan()
    hostile["advisory_only"] = False
    hostile["auto_load_allowed"] = True
    hostile["creates_provider_eligibility"] = True

    after = apply_qualification_to_benchmark_plan(hostile, report)

    assert after["advisory_only"] is True
    assert after["auto_load_allowed"] is False
    assert after["creates_provider_eligibility"] is False
    assert after["signed_admission_required"] is True


def test_plan_annotation_ignores_non_dict_requests():
    report = build_qualification_report(_evidence_rows())
    plan = _plan()
    plan["requests"].append("not-a-request")

    after = apply_qualification_to_benchmark_plan(plan, report)

    assert after["requests"][-1] == "not-a-request"


# --- response-derived evidence -------------------------------------------
#
# classify_failure reads failure_mode off the row, so a harness that asserts the
# wrong cause is believed. evidence_from_response inverts that: the caller says
# what came back and the outcome is derived. These pin the precedence and, most
# importantly, that a declared failure cannot override a derived one.


def test_derived_outcome_overrides_a_declared_failure_mode():
    row = _row(succeeded=False, failure_mode="test_failure", usable_content_chars=0)

    built = evidence_from_response(row, content=None, reasoning="thinking out loud")

    assert built["failure_mode"] == "reasoning_only_output"
    assert built["succeeded"] is False


def test_timeout_beats_every_other_signal():
    row = _row(succeeded=False, failure_mode="test_failure")

    built = evidence_from_response(
        row, content=None, reasoning="partial", finish_reason="length", timed_out=True
    )

    assert built["failure_mode"] == "timeout"


def test_length_truncation_beats_empty_content():
    built = evidence_from_response(
        _row(), content=None, reasoning=None, finish_reason="length"
    )

    assert built["failure_mode"] == "length_truncated"


def test_wrong_response_field_is_detected():
    built = evidence_from_response(
        _row(), content="actual answer", response_field_used="reasoning", expected_field="content"
    )

    assert built["failure_mode"] == "wrong_response_field"


def test_empty_content_with_populated_reasoning_is_reasoning_only():
    built = evidence_from_response(_row(), content="", reasoning="step 1\nstep 2")

    assert built["failure_mode"] == "reasoning_only_output"
    assert built["usable_content_chars"] == 0
    assert built["reasoning_chars"] == len("step 1\nstep 2")


def test_fully_empty_response_is_empty_usable_content():
    built = evidence_from_response(_row(), content=None, reasoning=None)

    assert built["failure_mode"] == "empty_usable_content"
    assert built["succeeded"] is False


def test_a_declared_failure_is_not_erased_by_a_clean_response():
    """Only the response can disprove a failure, never silently clear one.

    A patch can apply and still fail its tests, so a non-content failure the
    harness reports is real signal and is preserved. The mislabel hole this
    builder closes runs the other way: a harness cannot *relabel* a
    content-derived failure, because the derived mode always wins.
    """

    built = evidence_from_response(
        _row(failure_mode="test_failure"), content="a real grounded answer"
    )

    assert built["succeeded"] is False
    assert built["failure_mode"] == "test_failure"


def test_clean_response_succeeds_when_nothing_is_declared():
    built = evidence_from_response(_row(), content="a real grounded answer")

    assert built["succeeded"] is True
    assert built["failure_mode"] is None


def test_declared_failure_survives_when_the_response_itself_showed_nothing_wrong():
    """A harness still gets to explain a non-content failure."""

    built = evidence_from_response(_row(failure_mode="test_failure"), content="a patch")

    assert built["failure_mode"] == "test_failure"
    assert built["succeeded"] is False


def test_derived_rows_feed_straight_into_the_report():
    rows = [
        evidence_from_response(
            _row(test_level=level, succeeded=True, grounding_correct=True),
            content="ok",
            patch_valid=True if level == "L4_PATCH" else None,
            tests_passed=True if level == "L4_PATCH" else None,
        )
        for level in QUALIFICATION_LEVELS
    ]

    report = build_qualification_report(rows)
    entry = report["entries"][0]

    assert entry["role"] == "CODE_QUALIFIED"
    assert report["summary"]["rejected_rows"] == 0


def test_derived_evidence_still_refuses_to_buy_a_role_with_throughput():
    rows = [
        evidence_from_response(
            _row(test_level=level, tokens_per_second=9999.0, grounding_correct=True),
            content=None,
            reasoning="lots of thinking, no answer",
        )
        for level in QUALIFICATION_LEVELS
    ]

    report = build_qualification_report(rows)

    entry = report["entries"][0]
    assert entry["role"] == "UNQUALIFIED"
    assert entry["failures"]["counts"]["reasoning_only_output"] == len(QUALIFICATION_LEVELS)
    assert entry["failures"]["generic_buckets"] == []


# --- endpoint progress ladder -------------------------------------------


def test_endpoint_status_distinguishes_loaded_but_protocol_broken():
    broken = classify_endpoint_observation(
        {"endpoint_reached": True, "model_visible": True, "model_loaded": True,
         "protocol_usable": False}
    )
    working = classify_endpoint_observation(
        {"endpoint_reached": True, "model_visible": True, "model_loaded": True,
         "protocol_usable": True}
    )

    assert broken == "PROTOCOL_UNUSABLE"
    assert working == "PROTOCOL_USABLE"


def test_endpoint_status_does_not_round_an_unmeasured_rung_up():
    unmeasured = classify_endpoint_observation(
        {"endpoint_reached": True, "model_visible": True, "model_loaded": True}
    )

    assert unmeasured == "MODEL_LOADED"


@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        ({"endpoint_reached": False}, "UNREACHABLE"),
        ({"endpoint_reached": True}, "REACHABLE"),
        ({"endpoint_reached": True, "model_visible": True}, "MODEL_VISIBLE"),
    ],
)
def test_endpoint_status_stops_at_the_last_demonstrated_rung(observation, expected):
    assert classify_endpoint_observation(observation) == expected


def test_transport_axes_stay_independently_measurable():
    """The progress ladder must not collapse the independent axes.

    A harness that only reported model_visible has still measured
    model_visible; folding it into a cumulative ladder would destroy the
    distinction the capability block exists to keep.
    """

    caps = derive_capabilities(
        [_row(model_visible=True, endpoint_reached=None, model_loaded=None,
              protocol_usable=None, succeeded=False)]
    )

    # The independent axis records what was measured...
    assert caps["model_visible"] is True
    assert caps["model_loaded"] is False
    assert caps["endpoint_reachable"] is False
    # ...while the cumulative ladder starts from the first unproven rung rather
    # than rounding visibility up into a health claim.
    assert caps["endpoint_status"] == "UNREACHABLE"
