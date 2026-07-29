from auto_router.model_value import build_value_matrix


def sample(node: str, model: str, tps: float, ok: bool = True) -> dict:
    return {
        "provider_id": node,
        "model_id": model,
        "tokens_per_second": tps,
        "status_code": 200 if ok else 500,
        "error_type": None if ok else "provider_error",
    }


def test_value_matrix_recommends_best_measured_model_and_preserves_unmeasured() -> None:
    reports = [
        {"hostname": "fast", "loaded": ["strong", "new"]},
        {"hostname": "slow", "loaded": ["weak"]},
    ]
    samples = (
        [sample("fast", "strong", 25.0) for _ in range(12)]
        + [sample("slow", "weak", 2.0) for _ in range(12)]
    )

    result = build_value_matrix(reports, samples)
    entries = {(row["node_id"], row["model_id"]): row for row in result["entries"]}

    assert entries[("fast", "strong")]["recommendation"] == "keep_hot"
    assert entries[("slow", "weak")]["recommendation"] == "unload_candidate"
    assert entries[("fast", "new")]["recommendation"] == "benchmark"
    assert entries[("slow", "weak")]["opportunity_cost_rvu_per_hour"] > 0


def test_failures_reduce_effective_value_and_trigger_candidate() -> None:
    samples = [sample("node", "flaky", 20.0, ok=i < 6) for i in range(12)]
    result = build_value_matrix([{"hostname": "node", "loaded": ["flaky"]}], samples)

    assert result["entries"][0]["success_rate"] == 0.5
    assert result["entries"][0]["recommendation"] == "unload_candidate"
