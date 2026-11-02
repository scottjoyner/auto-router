from __future__ import annotations

import pytest

from scripts import build_fleet_loadouts as builder
from scripts.build_fleet_loadouts import Candidate, LoadoutPlan, NodeInfo, build_report, plan_loadout


def _node(
    name: str,
    ip: str,
    models: list[str],
    latency_ms: float,
    online: bool = True,
) -> NodeInfo:
    return NodeInfo(
        name=name,
        ip=ip,
        online=online,
        loaded_models=models,
        all_models=models,
        configured_models=models,
        latency_ms=latency_ms,
        error="",
        power_watts=45.0,
        discovery_source="live",
        inventory_complete=True,
        inventory_authoritative=True,
        loaded_state_source="native",
        observed_at="2026-08-05T01:00:00+00:00",
    )


def _stats() -> dict[str, object]:
    return {
        "queues": {"worker": 12, "review": 3},
        "stats": {
            "by_node": {"beelink-ryzen-7-mini-pc": 120, "x1-370": 24, "xwing": 18},
            "by_role": {"worker": 120, "reviewer": 42},
            "by_stage": {"work": 128, "review": 34},
        },
        "slots": [
            {"node": "beelink-ryzen-7-mini-pc", "model": "vibethinker-3b-hermes", "role": "worker", "completed": 120, "success": 114, "failure": 6},
            {"node": "x1-370", "model": "ornith-1.0-35b", "role": "reviewer", "completed": 24, "success": 23, "failure": 1},
            {"node": "xwing", "model": "ornith-1.0-35b", "role": "reviewer", "completed": 18, "success": 17, "failure": 1},
        ],
    }


def test_plan_loadout_prefers_worker_then_reviewer_for_strict_review() -> None:
    nodes = [
        _node("beelink-ryzen-7-mini-pc", "100.85.72.121", ["vibethinker-3b-hermes"], 44.0),
        _node("x1-370", "100.64.43.123", ["ornith-1.0-35b"], 120.0),
        _node("xwing", "100.108.99.47", ["ornith-1.0-35b"], 108.0),
    ]
    plan = plan_loadout(
        {"id": "coding_review_strict", "name": "Coding / strict review"},
        nodes,
        _stats(),
    )

    assert plan.primary is not None
    assert plan.primary.node_name == "beelink-ryzen-7-mini-pc"
    assert plan.reviewer is not None
    assert plan.reviewer.role == "reviewer"
    assert plan.reviewer.node_name in {"x1-370", "xwing"}
    assert plan.reviewer.node_name != plan.primary.node_name


def test_build_report_returns_one_loadout_per_profile() -> None:
    nodes = [
        _node("beelink-ryzen-7-mini-pc", "100.85.72.121", ["vibethinker-3b-hermes"], 40.0),
        _node("x1-370", "100.64.43.123", ["ornith-1.0-35b"], 112.0),
    ]
    task_profiles = [
        {"id": "summary_extraction", "name": "Summary / extraction"},
        {"id": "planning_strategy", "name": "Planning / strategy"},
    ]

    report, plans = build_report(nodes, _stats(), task_profiles)

    assert report["loadouts"]
    assert len(plans) == 2
    assert {item["task_profile_id"] for item in report["loadouts"]} == {"summary_extraction", "planning_strategy"}


def test_multilane_discovery_does_not_overwrite_same_physical_graph_identity() -> None:
    # A user can inspect many lanes of one machine, but a graph state keyed by
    # that machine cannot safely store duplicate node rows without reconciliation.
    node_rows = [
        {"name": "xwing", "provider_name": "xwing-main", "online": True,
         "inventory_authoritative": True, "loaded_models": ["model-a"]},
        {"name": "xwing", "provider_name": "xwing-bonsai", "online": True,
         "inventory_authoritative": True, "loaded_models": ["model-b"]},
    ]
    with pytest.raises(builder.UnsafeFleetSnapshotError, match="duplicate physical node identities: xwing"):
        builder._validate_snapshot_for_persistence(
            node_rows, builder._authoritative_model_rows(node_rows), [],
            allow_empty_snapshot=True,
        )

    class NeverTouchDriver:
        def execute_query(self, *args, **kwargs):
            raise AssertionError("graph write/setup was reached before collision rejection")
        def session(self, *args, **kwargs):
            raise AssertionError("database session was reached before collision rejection")

    with pytest.raises(builder.UnsafeFleetSnapshotError, match="lane-aware graph reconciliation"):
        builder.persist_to_neo4j(
            NeverTouchDriver(), {"nodes": node_rows}, [], [],
            allow_empty_snapshot=True, allow_degraded_snapshot=True,
        )
