from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_fleet_loadouts_atomic.py"
SOURCE = SCRIPT.read_text(encoding="utf-8")


def test_node_state_identity_is_snapshot_scoped() -> None:
    assert "MERGE (n:FleetNodeState {\n            snapshot_id: $snapshot_id,\n            node_name: row.name" in SOURCE


def test_model_state_identity_is_snapshot_scoped() -> None:
    assert "MERGE (m:FleetModelState {\n            snapshot_id: $snapshot_id,\n            node_name: row.node_name,\n            model_id: row.model_id" in SOURCE


def test_assignment_matches_same_snapshot_state() -> None:
    assert "MATCH (n:FleetNodeState {\n                    snapshot_id: $snapshot_id" in SOURCE
    assert "MATCH (m:FleetModelState {\n                    snapshot_id: $snapshot_id" in SOURCE


def test_writer_uses_managed_transaction() -> None:
    assert "session.execute_write(" in SOURCE


def test_writer_does_not_delete_historical_state() -> None:
    assert "DETACH DELETE n" not in SOURCE
    assert "DETACH DELETE m" not in SOURCE


def test_report_exposes_commit_state() -> None:
    assert 'report["graph_committed"] = False' in SOURCE
    assert 'report["build_status"] = "persistence_failed"' in SOURCE
    assert 'report["build_status"] = "committed"' in SOURCE
    assert 'report["graph_committed"] = True' in SOURCE
