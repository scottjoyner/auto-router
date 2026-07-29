from auto_router.benchmark_planner import build_benchmark_plan
from auto_router.quality_evidence import aggregate_quality, outcome_quality


def test_outcome_quality_honors_validation_acceptance_and_repairs() -> None:
    score, reasons = outcome_quality({
        "success": True,
        "validation_passed": True,
        "retry_path": ["repair"],
        "metadata": {"quality_score": 0.95, "user_accepted": True},
    })

    assert score == 0.855
    assert "explicit evaluator score" in reasons
    assert "user accepted" in reasons


def test_quality_aggregation_is_task_specific() -> None:
    evidence = aggregate_quality([
        {
            "model": "qwen",
            "node_id": "x1",
            "success": True,
            "validation_passed": True,
            "created_at": "2026-07-29T00:00:00+00:00",
            "metadata": {"task_family": "code", "quality_score": 0.9},
        },
        {
            "model": "qwen",
            "node_id": "x1",
            "success": False,
            "validation_passed": False,
            "created_at": "2026-07-29T01:00:00+00:00",
            "metadata": {"task_family": "summarize"},
        },
    ])

    entries = {row["task_family"]: row for row in evidence["entries"]}
    assert entries["coding"]["quality_score"] == 0.9
    assert entries["summarization"]["quality_score"] == 0.15


def test_benchmark_plan_only_targets_loaded_models_and_never_loads() -> None:
    matrix = {
        "entries": [
            {"node_id": "x1", "model_id": "hot", "loaded": True, "online": True, "sample_count": 0},
            {"node_id": "x1", "model_id": "cold", "loaded": False, "online": True, "sample_count": 0},
        ]
    }
    plan = build_benchmark_plan(matrix, {"entries": []})

    assert plan["requests"]
    assert {row["model_id"] for row in plan["requests"]} == {"hot"}
    assert all(row["execution_mode"] == "dry_run" for row in plan["requests"])
    assert all(row["requires_model_load"] is False for row in plan["requests"])
    assert plan["auto_load_allowed"] is False
