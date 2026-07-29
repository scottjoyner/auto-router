from auto_router.fleet_health import build_health_plan


def test_health_plan_detects_offline_stale_and_repeated_failures():
    topology = {"nodes": [
        {"id": "offline", "online": False, "report_fresh": False, "loaded_models": []},
        {"id": "stale", "online": True, "report_fresh": False, "loaded_models": ["m"]},
        {"id": "flaky", "online": True, "report_fresh": True, "loaded_models": ["m"]},
    ]}
    samples = [
        {"provider_id": "flaky", "status_code": 500, "error_type": "error"}
        for _ in range(3)
    ]

    result = build_health_plan(topology, {"entries": []}, samples)
    kinds = {(row["node_id"], row["incident_type"]) for row in result["incidents"]}

    assert ("offline", "node_offline") in kinds
    assert ("stale", "stale_report") in kinds
    assert ("flaky", "repeated_runtime_failures") in kinds
    assert result["automatic_quarantine"] is False
