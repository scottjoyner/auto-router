from auto_router.task_contract import build_task_contract


def test_build_task_contract_normalizes_coding_to_code() -> None:
    contract = build_task_contract({"task_kind": "coding"})

    assert contract["task_kind"] == "code"
    assert contract["requires_tools"] is True
    assert contract["capability_lane"] == "tool_required"
    assert contract["plan_steps"][0] == "Inspect the current state one slice at a time."
    assert contract["validation_metrics"] == ["acceptance_criteria_met", "regressions_checked", "handoff_ready"]


def test_build_task_contract_marks_handoff_for_finalized_work() -> None:
    contract = build_task_contract({"task_kind": "review", "finalized": True})

    assert contract["workflow_stage"] == "handoff"
    assert contract["requires_tools"] is True
    assert contract["review_checkpoints"][-1] == "final handoff approved"


def test_build_task_contract_uses_research_defaults() -> None:
    contract = build_task_contract({"task": "Research the routing lane setup"})

    assert contract["task_kind"] == "research"
    assert contract["evidence_required"] is True
    assert contract["plan_steps"][0] == "Gather the relevant evidence or context."
    assert contract["validation_metrics"] == ["evidence_captured", "claims_supported", "final_answer_ready"]
