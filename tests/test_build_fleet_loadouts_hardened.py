from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARDENED = (ROOT / "scripts" / "build_fleet_loadouts_hardened.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "scripts" / "run_fleet_loadout_rebuild.sh").read_text(encoding="utf-8")
PREFLIGHT = (ROOT / "scripts" / "preflight_fleet_loadouts.py").read_text(encoding="utf-8")


def test_task_profiles_use_selected_database() -> None:
    assert "def read_task_profiles(driver, database: str)" in HARDENED
    assert "driver.session(database=database)" in HARDENED


def test_report_write_is_atomic() -> None:
    assert "tempfile.mkstemp" in HARDENED
    assert "os.fsync" in HARDENED
    assert "os.replace" in HARDENED


def test_health_gates_run_before_persistence() -> None:
    assert HARDENED.index("enforce_health_gates(") < HARDENED.index("persist_to_neo4j_atomic(", HARDENED.index("def main"))


def test_post_commit_verification_exists() -> None:
    assert "def verify_snapshot" in HARDENED
    assert "post-commit verification failed" in HARDENED


def test_current_snapshot_pointer_exists() -> None:
    assert "FleetSnapshotPointer" in HARDENED
    assert "POINTS_TO" in HARDENED


def test_runner_prevents_overlap_and_uses_hardened_writer() -> None:
    assert "flock -n" in RUNNER
    assert "build_fleet_loadouts_hardened.py" in RUNNER


def test_runner_disables_automatic_retention_by_default() -> None:
    assert 'AUTO_ROUTER_RETAIN_SNAPSHOTS:-0' in RUNNER


def test_preflight_checks_composite_constraints() -> None:
    assert "FleetNodeState(snapshot_id,node_name)" in PREFLIGHT
    assert "FleetModelState(snapshot_id,node_name,model_id)" in PREFLIGHT
