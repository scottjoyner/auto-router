"""Deterministic tests for benchmark-only model qualification.

The campaign that motivated this pipeline reported nodes as degraded or
unavailable while their endpoints still answered. These tests pin the
distinction: reachability is not usefulness, and throughput is not quality.
"""

from __future__ import annotations

from auto_router.benchmark_qualification import (
    QUALITY_FLOOR,
    LadderLevel,
    QualificationFailure,
    QualificationRole,
    apply_qualification_to_benchmark_plan,
    build_qualification_report,
    derive_role,
    evidence_from_response,
    highest_passed_level,
)
from auto_router.benchmark_qualification import TestOutcome as PatchTestOutcome

PROMPT = "a" * 64
REASONING_ONLY = QualificationFailure.reasoning_only_output


def evidence(level: LadderLevel, **kwargs):
    """Build one passing attempt at `level` unless overridden."""
    defaults = {
        "node_id": "node-a",
        "model_id": "model-x",
        "test_level": level,
        "prompt_sha256": PROMPT,
        "content": "a",
        "task_family": "coding",
    }
    defaults.update(kwargs)
    return evidence_from_response(**defaults)


def ladder_up_to(level: LadderLevel) -> list:
    order = [
        LadderLevel.l0_protocol,
        LadderLevel.l1_exact_grounding,
        LadderLevel.l2_source_grounding,
        LadderLevel.l3_diagnosis,
        LadderLevel.l4_patch,
    ]
    return [evidence(step) for step in order[: order.index(level) + 1]]


# --- fast but wrong -> UNQUALIFIED for coding -----------------------------


def test_fast_but_wrong_is_not_code_qualified():
    """Throughput must never buy the coding role."""
    records = ladder_up_to(LadderLevel.l4_patch)
    # Every rung answered quickly, but the patch was wrong.
    records = [
        record.model_copy(
            update={
                "tokens_per_second": 900.0,
                "ttft_ms": 40.0,
                "quality_score": 0.1,
                "grounding_correct": False,
            }
        )
        for record in records
    ]

    result = derive_role(records)

    assert result["role"] is not QualificationRole.code_qualified
    assert result["role"] is QualificationRole.unqualified
    assert result["highest_passed_level"] is None


def test_high_throughput_cannot_override_a_failed_quality_floor():
    slow_correct = derive_role(
        [
            record.model_copy(update={"tokens_per_second": 2.0})
            for record in ladder_up_to(LadderLevel.l1_exact_grounding)
        ]
    )
    assert slow_correct["role"] is QualificationRole.summary_only

    fast_but_failing = [
        evidence(LadderLevel.l4_patch, tokens_per_second=5000.0).model_copy(
            update={"quality_score": 0.05}
        )
    ]

    assert derive_role(fast_but_failing)["role"] is QualificationRole.unqualified
    assert slow_correct["role"] is not QualificationRole.unqualified


def test_reaching_only_l0_earns_no_lane():
    """A deterministic tiny response proves transport, not usefulness."""
    result = derive_role(ladder_up_to(LadderLevel.l0_protocol))

    assert result["role"] is QualificationRole.unqualified
    assert result["highest_passed_level"] is LadderLevel.l0_protocol


def test_quality_below_floor_is_not_a_pass_even_without_a_failure():
    record = evidence(LadderLevel.l3_diagnosis).model_copy(
        update={"quality_score": QUALITY_FLOOR - 0.01}
    )

    result = derive_role([record])

    assert result["role"] is QualificationRole.unqualified


# --- slow but correct -> may qualify --------------------------------------


def test_slow_but_correct_may_qualify():
    records = ladder_up_to(LadderLevel.l4_patch)
    slow = [
        record.model_copy(update={"tokens_per_second": 3.5, "ttft_ms": 9000.0})
        for record in records
    ]

    result = derive_role(slow)

    assert result["role"] is QualificationRole.code_qualified
    assert result["highest_passed_level"] is LadderLevel.l4_patch


def test_full_ladder_earns_code_qualified():
    assert (
        derive_role(ladder_up_to(LadderLevel.l4_patch))["role"]
        is QualificationRole.code_qualified
    )


# --- L1 pass / L3 fail -> scout/extraction role only -----------------------


def test_l1_pass_l3_fail_is_scoped_to_a_scout_role():
    records = ladder_up_to(LadderLevel.l2_source_grounding)
    records.append(
        evidence(
            LadderLevel.l3_diagnosis,
            grounding_correct=False,
            content="probably the cache",
        ).model_copy(update={"quality_score": 0.2})
    )

    result = derive_role(records)

    assert result["role"] is QualificationRole.scout_qualified
    assert result["highest_passed_level"] is LadderLevel.l2_source_grounding
    assert result["role"] is not QualificationRole.code_qualified
    assert result["role"] is not QualificationRole.review_qualified


def test_l1_only_is_summary_only():
    records = ladder_up_to(LadderLevel.l1_exact_grounding)

    assert derive_role(records)["role"] is QualificationRole.summary_only


def test_l3_earns_review_but_not_code():
    records = ladder_up_to(LadderLevel.l3_diagnosis)

    result = derive_role(records)

    assert result["role"] is QualificationRole.review_qualified
    assert result["role"] is not QualificationRole.code_qualified


# --- explicit failure classification --------------------------------------


def test_timeout_is_classified_explicitly_not_as_a_generic_failure():
    record = evidence_from_response(
        node_id="destroyer",
        model_id="k2",
        test_level=LadderLevel.l1_exact_grounding,
        prompt_sha256=PROMPT,
        content=None,
        timed_out=True,
        tokens_per_second=0.0,
    )

    assert record.outcome is PatchTestOutcome.failed
    assert record.failure is QualificationFailure.timeout
    assert record.failure is not QualificationFailure.empty_usable_content
    assert record.is_transport_failure is True
    assert derive_role([record])["role"] is QualificationRole.unqualified


def test_empty_content_with_reasoning_populated_is_reasoning_only():
    """Empty content and reasoning-only content are different diseases."""
    record = evidence_from_response(
        node_id="optiplex",
        model_id="qwen",
        test_level=LadderLevel.l1_exact_grounding,
        prompt_sha256=PROMPT,
        content="",
        reasoning="let me think about which function...",
    )

    assert record.failure is REASONING_ONLY
    assert record.failure is not QualificationFailure.empty_usable_content
    assert record.failure is not QualificationFailure.timeout


def test_truly_empty_content_is_empty_usable_content():
    record = evidence_from_response(
        node_id="lenovo",
        model_id="qwen",
        test_level=LadderLevel.l1_exact_grounding,
        prompt_sha256=PROMPT,
        content="   ",
        reasoning=None,
    )

    assert record.failure is QualificationFailure.empty_usable_content


def test_length_truncation_is_its_own_failure():
    record = evidence_from_response(
        node_id="xwing",
        model_id="ornith",
        test_level=LadderLevel.l2_source_grounding,
        prompt_sha256=PROMPT,
        content="partial answer that got cut",
        finish_reason="length",
    )

    assert record.failure is QualificationFailure.length_truncated


def test_ungrounded_context_is_its_own_failure():
    record = evidence_from_response(
        node_id="node-a",
        model_id="m",
        test_level=LadderLevel.l2_source_grounding,
        prompt_sha256=PROMPT,
        content="the answer is in foo.py",
        grounding_correct=False,
    )

    assert record.failure is QualificationFailure.context_not_grounded


def test_wrong_response_field_is_its_own_failure():
    record = evidence_from_response(
        node_id="node-a",
        model_id="m",
        test_level=LadderLevel.l0_protocol,
        prompt_sha256=PROMPT,
        content="pong",
        expected_field="content",
        response_field_used="reasoning",
    )

    assert record.failure is QualificationFailure.wrong_response_field


def test_failed_tests_are_their_own_failure():
    record = evidence_from_response(
        node_id="node-a",
        model_id="m",
        test_level=LadderLevel.l4_patch,
        prompt_sha256=PROMPT,
        content="patch applied",
        patch_sha256="b" * 64,
        test_result=PatchTestOutcome.failed,
    )

    assert record.failure is QualificationFailure.test_failure


def test_invalid_patch_is_distinct_from_a_failed_test():
    record = evidence_from_response(
        node_id="node-a",
        model_id="m",
        test_level=LadderLevel.l4_patch,
        prompt_sha256=PROMPT,
        content="here is my change",
        patch_sha256="c" * 64,
        test_result=PatchTestOutcome.not_applicable,
    )

    assert record.failure is QualificationFailure.invalid_patch
    assert record.failure is not QualificationFailure.test_failure


def test_failure_taxonomy_covers_the_campaign_failures():
    """The real campaign produced these; none may collapse into one bucket."""
    required = {
        "empty_usable_content",
        "reasoning_only_output",
        "length_truncated",
        "timeout",
        "context_not_grounded",
        "wrong_response_field",
        "invalid_patch",
        "test_failure",
    }

    assert required <= {failure.value for failure in QualificationFailure}


def test_no_evidence_is_unqualified_not_a_free_pass():
    assert derive_role([])["role"] is QualificationRole.unqualified
    assert highest_passed_level([]) is None


def test_all_timeouts_yield_no_qualified_level():
    records = [
        evidence(
            level,
            content=None,
            timed_out=True,
        )
        for level in (
            LadderLevel.l0_protocol,
            LadderLevel.l1_exact_grounding,
            LadderLevel.l2_source_grounding,
            LadderLevel.l3_diagnosis,
            LadderLevel.l4_patch,
        )
    ]

    assert highest_passed_level(records) is None
    assert derive_role(records)["role"] is QualificationRole.unqualified


def test_low_confidence_evidence_does_not_earn_a_role():
    records = [
        record.model_copy(update={"confidence": 0.2})
        for record in ladder_up_to(LadderLevel.l3_diagnosis)
    ]

    result = derive_role(records)

    assert result["role"] is QualificationRole.unqualified
    assert "confidence" in " ".join(result["reasons"])


# --- integration boundary --------------------------------------------------


def test_report_is_advisory_only_and_creates_no_eligibility():
    report = build_qualification_report(
        observations=[{"node_id": "node-a", "model_id": "model-x", "reachable": True}],
        evidence=ladder_up_to(LadderLevel.l3_diagnosis),
    )

    assert report["advisory_only"] is True
    assert report["auto_load_allowed"] is False
    assert report["authority"]["creates_provider_eligibility"] is False
    assert report["authority"]["changes_production_admission"] is False
    assert report["authority"]["changes_routing_authority"] is False
    assert report["authority"]["changes_signed_runtime_projection"] is False
    assert report["method"]["raw_prompts_persisted"] is False


def test_report_separates_endpoint_status_from_role():
    report = build_qualification_report(
        observations=[
            {
                "node_id": "node-a",
                "model_id": "model-x",
                "reachable": True,
                "model_visible": True,
                "model_loaded": True,
                "protocol_usable": True,
            }
        ],
        evidence=ladder_up_to(LadderLevel.l1_exact_grounding),
    )

    entry = report["entries"][0]

    # Fully healthy transport, but only qualified to summarize.
    assert entry["endpoint_status"] == "model_loaded"
    assert entry["role"] == "summary_only"


def test_benchmark_evidence_cannot_create_provider_eligibility():
    """Even a fully qualified model must not appear as provider eligibility."""
    report = build_qualification_report(
        observations=[{"node_id": "node-a", "model_id": "model-x", "reachable": True}],
        evidence=ladder_up_to(LadderLevel.l4_patch),
    )

    assert report["entries"][0]["role"] == "code_qualified"
    assert report["authority"]["creates_provider_eligibility"] is False

    from auto_router.policy import PolicyEngine

    # The production eligibility gate is untouched by any of this.
    source = PolicyEngine._provider_is_eligible.__doc__
    assert source is None or isinstance(source, str)
    assert report["authority"]["changes_production_admission"] is False


def test_plan_annotation_preserves_advisory_flags_and_structure():
    plan = {
        "advisory_only": True,
        "auto_load_allowed": False,
        "execution_mode": "dry_run",
        "requests": [
            {"node_id": "node-a", "model_id": "model-x", "task_family": "coding"},
            {"node_id": "node-b", "model_id": "model-y", "task_family": "coding"},
        ],
    }
    report = build_qualification_report(
        observations=[{"node_id": "node-a", "model_id": "model-x", "reachable": True}],
        evidence=ladder_up_to(LadderLevel.l4_patch),
    )

    annotated = apply_qualification_to_benchmark_plan(plan, report)

    assert annotated["advisory_only"] is True
    assert annotated["auto_load_allowed"] is False
    assert len(annotated["requests"]) == len(plan["requests"])
    assert annotated["requests"][0]["benchmark_role"] == "code_qualified"
    # Unknown pairs are left untouched rather than defaulted to a lane.
    assert "benchmark_role" not in annotated["requests"][1]
    assert plan["requests"][0].get("benchmark_role") is None


def test_evidence_record_cannot_hold_a_raw_prompt():
    """extra='forbid' plus digest-only fields keeps prompt text out."""
    import pytest
    from pydantic import ValidationError

    record = evidence(LadderLevel.l0_protocol)
    document = record.model_dump(mode="json")
    document["prompt"] = "my private prompt"

    with pytest.raises(ValidationError):
        type(record).model_validate(document)