from auto_router.loadout_optimizer import estimate_model_ram_gib, simulate_loadout


def test_model_memory_estimate_uses_parameters_and_quantization():
    assert estimate_model_ram_gib("qwen-35b-q4") > estimate_model_ram_gib("qwen-7b-q4")


def test_simulation_protects_last_copy_and_proposes_safe_replication():
    reports = [
        {
            "hostname": "x1",
            "library": ["strong-7b-q4", "weak-1b-q4"],
            "loaded": ["strong-7b-q4", "weak-1b-q4"],
            "specs": {"ram_gib": 96},
        },
        {
            "hostname": "xwing",
            "library": ["strong-7b-q4"],
            "loaded": [],
            "specs": {"ram_gib": 32},
        },
    ]
    matrix = {
        "entries": [
            {
                "node_id": "x1", "model_id": "strong-7b-q4", "loaded": True,
                "recommendation": "keep_hot", "confidence": 1.0,
                "effective_rvu_per_hour": 100,
            },
            {
                "node_id": "x1", "model_id": "weak-1b-q4", "loaded": True,
                "recommendation": "unload_candidate", "confidence": 1.0,
                "effective_rvu_per_hour": 5,
            },
        ]
    }

    result = simulate_loadout(reports, matrix, {"entries": []})
    actions = {(row["node_id"], row["model_id"]): row for row in result["actions"]}

    assert result["executable"] is False
    assert actions[("x1", "weak-1b-q4")]["action"] == "defer"
    assert actions[("xwing", "strong-7b-q4")]["action"] == "replicate_candidate"
    assert actions[("xwing", "strong-7b-q4")]["requires_approval"] is True


def test_redundant_low_value_copy_can_be_unload_candidate():
    reports = [
        {"hostname": "a", "loaded": ["other", "weak"], "library": ["other", "weak"], "specs": {"ram_gib": 32}},
        {"hostname": "b", "loaded": ["weak"], "library": ["weak"], "specs": {"ram_gib": 32}},
    ]
    matrix = {"entries": [{
        "node_id": "a", "model_id": "weak", "loaded": True,
        "recommendation": "unload_candidate", "confidence": 1.0,
    }]}

    result = simulate_loadout(reports, matrix, {"entries": []})
    action = next(row for row in result["actions"] if row["node_id"] == "a" and row["model_id"] == "weak")
    assert action["action"] == "unload_candidate"
