#!/usr/bin/env python3
"""Apply schema-enforced reconciliation and fenced report publication."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_fleet_loadouts.py"
TEST = ROOT / "tests" / "test_fleet_loadout_neo4j_integration.py"
README = ROOT / "README.md"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {label} match, found {count}")
    return text.replace(old, new, 1)


def replace_count(text: str, old: str, new: str, *, count: int, label: str) -> str:
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(f"expected {count} {label} matches, found {actual}")
    return text.replace(old, new)


def patch_builder() -> None:
    text = BUILDER.read_text(encoding="utf-8")

    text = replace_once(
        text,
        'DEFAULT_MAX_FLEET_DROP_FRACTION = float(os.getenv("AUTO_ROUTER_MAX_FLEET_DROP_FRACTION", "0.5"))\n',
        '''DEFAULT_MAX_FLEET_DROP_FRACTION = float(os.getenv("AUTO_ROUTER_MAX_FLEET_DROP_FRACTION", "0.5"))
RECONCILIATION_SCHEMA_QUERIES = (
    """
    CREATE CONSTRAINT fleet_reconciliation_lock_name IF NOT EXISTS
    FOR (n:FleetReconciliationLock) REQUIRE n.name IS UNIQUE
    """,
    """
    CREATE CONSTRAINT fleet_snapshot_id IF NOT EXISTS
    FOR (n:FleetSnapshot) REQUIRE n.snapshot_id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT fleet_node_state_name IF NOT EXISTS
    FOR (n:FleetNodeState) REQUIRE n.node_name IS UNIQUE
    """,
    """
    CREATE CONSTRAINT fleet_node_observation_id IF NOT EXISTS
    FOR (n:FleetNodeObservation) REQUIRE n.observation_id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT fleet_model_state_id IF NOT EXISTS
    FOR (n:FleetModelState) REQUIRE n.state_id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT fleet_model_observation_id IF NOT EXISTS
    FOR (n:FleetModelObservation) REQUIRE n.observation_id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT fleet_loadout_id IF NOT EXISTS
    FOR (n:FleetLoadout) REQUIRE n.loadout_id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT fleet_loadout_assignment_id IF NOT EXISTS
    FOR (n:FleetLoadoutAssignment) REQUIRE n.assignment_id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT fleet_change_delta_id IF NOT EXISTS
    FOR (n:FleetChangeDelta) REQUIRE n.delta_id IS UNIQUE
    """,
)
''',
        label="reconciliation schema constants",
    )

    schema_helper = '''def _ensure_reconciliation_schema(
    driver,
    *,
    database: str,
) -> None:
    """Fail closed unless every reconciliation identity is schema-unique."""

    try:
        with driver.session(database=database) as session:
            for query in RECONCILIATION_SCHEMA_QUERIES:
                session.run(query).consume()
    except Exception as exc:
        raise UnsafeFleetSnapshotError(
            "cannot enforce Neo4j reconciliation uniqueness constraints"
        ) from exc
'''
    text = replace_once(
        text,
        "\ndef _locked_current_snapshot_info(tx, snapshot_id: str) -> dict[str, Any]:\n",
        "\n" + schema_helper + "\n\ndef _locked_current_snapshot_info(tx, snapshot_id: str) -> dict[str, Any]:\n",
        label="schema helper insertion",
    )

    text = replace_count(
        text,
        "ORDER BY coalesce(s.current, false) DESC, s.captured_at_ms DESC",
        "ORDER BY coalesce(s.current, false) DESC, "
        "coalesce(s.reconciliation_lock_version, 0) DESC, s.captured_at_ms DESC",
        count=2,
        label="snapshot ordering",
    )

    text = replace_once(
        text,
        '''    _validate_snapshot_for_persistence(
        node_rows,
        model_rows,
        plans,
        allow_empty_snapshot=allow_empty_snapshot,
    )

    node_state_rows: list[dict[str, Any]] = []
''',
        '''    _validate_snapshot_for_persistence(
        node_rows,
        model_rows,
        plans,
        allow_empty_snapshot=allow_empty_snapshot,
    )
    _ensure_reconciliation_schema(driver, database=database)

    node_state_rows: list[dict[str, Any]] = []
''',
        label="schema enforcement call",
    )

    publisher = '''def publish_committed_report(
    driver,
    path: Path,
    report: dict[str, Any],
    persistence: dict[str, Any],
    *,
    database: str = DEFAULT_NEO4J_DB,
) -> bool:
    """Publish only while the persisted snapshot still owns the reconciliation fence.

    The transaction takes the same singleton write lock used by reconcilers. A
    newer reconciliation therefore either waits for this report write and then
    supersedes it, or commits first and causes this stale publisher to skip.
    """

    snapshot_id = str(persistence.get("snapshot_id") or "")
    lock_version = persistence.get("reconciliation_lock_version")
    if not snapshot_id or lock_version is None:
        raise ValueError("persistence result is missing snapshot fencing metadata")

    def publish(tx) -> bool:
        current = tx.run(
            """
            MATCH (lock:FleetReconciliationLock {name: 'fleet-loadout'})
            SET lock.report_publisher_snapshot_id = $snapshot_id,
                lock.report_publisher_checked_at = datetime()
            WITH lock
            MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
            WHERE s.status = 'ready'
              AND s.current = true
              AND s.reconciliation_lock_version = $lock_version
            RETURN s.snapshot_id AS snapshot_id
            """,
            snapshot_id=snapshot_id,
            lock_version=lock_version,
        ).single()
        if not current:
            return False

        atomic_write_json(path, report)
        tx.run(
            """
            MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
            SET s.report_published_at = datetime(),
                s.report_path = $report_path,
                s.updated_at = datetime()
            """,
            snapshot_id=snapshot_id,
            report_path=str(path),
        ).consume()
        return True

    with driver.session(database=database) as session:
        return bool(session.execute_write(publish))
'''
    text = replace_once(
        text,
        "\ndef build_report(\n",
        "\n" + publisher + "\n\ndef build_report(\n",
        label="fenced report publisher",
    )

    text = replace_once(
        text,
        '''        report["persistence"] = persist_result
        report["publication"] = {
            "mode": "committed",
            "graph_persisted": True,
        }
        atomic_write_json(args.report_path, report)
        print("")
''',
        '''        report["persistence"] = persist_result
        report["publication"] = {
            "mode": "committed",
            "graph_persisted": True,
            "snapshot_id": persist_result["snapshot_id"],
            "reconciliation_lock_version": persist_result[
                "reconciliation_lock_version"
            ],
        }
        report_published = publish_committed_report(
            driver,
            args.report_path,
            report,
            persist_result,
            database=args.neo4j_database,
        )
        print("")
''',
        label="fenced main report publication",
    )

    text = replace_once(
        text,
        '''        if persist_result.get("previous_snapshot_id"):
            print(f"Previous snapshot: {persist_result['previous_snapshot_id']}")
        return 0
''',
        '''        if persist_result.get("previous_snapshot_id"):
            print(f"Previous snapshot: {persist_result['previous_snapshot_id']}")
        if not report_published:
            print(
                "Skipped JSON report publication because a newer fleet snapshot "
                "already owns the reconciliation fence"
            )
        return 0
''',
        label="superseded report diagnostic",
    )

    BUILDER.write_text(text, encoding="utf-8")


def patch_test() -> None:
    text = TEST.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''import importlib.util
import os
import sys
from pathlib import Path
''',
        '''import importlib.util
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
''',
        label="integration concurrency imports",
    )

    text = replace_once(
        text,
        '''               OR n:FleetTaskProfile
            DETACH DELETE n
''',
        '''               OR n:FleetTaskProfile
               OR n:FleetReconciliationLock
            DETACH DELETE n
''',
        label="lock cleanup",
    )

    text = replace_once(
        text,
        '''        assert second["previous_snapshot_id"] == first["snapshot_id"]
        with driver.session(database=database) as session:
''',
        '''        assert first["reconciliation_lock_version"] == 1
        assert second["previous_snapshot_id"] == first["snapshot_id"]
        assert second["reconciliation_lock_version"] == 2
        with driver.session(database=database) as session:
''',
        label="sequential fencing assertions",
    )

    text = replace_once(
        text,
        '''            assert set(snapshot_counts["statuses"]) == {"ready"}

            current_models = session.run(
''',
        '''            assert set(snapshot_counts["statuses"]) == {"ready"}

            lock_state = session.run(
                """
                MATCH (lock:FleetReconciliationLock {name: 'fleet-loadout'})
                RETURN count(lock) AS lock_count, max(lock.version) AS version
                """
            ).single(strict=True)
            assert lock_state["lock_count"] == 1
            assert lock_state["version"] == 2

            current_models = session.run(
''',
        label="sequential lock state assertions",
    )

    concurrent_test = r'''


def test_concurrent_reconciliations_are_fenced_and_report_cannot_regress(tmp_path: Path) -> None:
    uri = os.environ["NEO4J_URI"]
    user = os.environ["NEO4J_USER"]
    password = os.environ["NEO4J_PASSWORD"]
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    expected_constraints = {
        "fleet_reconciliation_lock_name",
        "fleet_snapshot_id",
        "fleet_node_state_name",
        "fleet_node_observation_id",
        "fleet_model_state_id",
        "fleet_model_observation_id",
        "fleet_loadout_id",
        "fleet_loadout_assignment_id",
        "fleet_change_delta_id",
    }
    try:
        driver.verify_connectivity()
        _clear_fleet_graph(driver, database)
        with driver.session(database=database) as session:
            for name in expected_constraints:
                session.run(f"DROP CONSTRAINT `{name}` IF EXISTS").consume()

        profiles = [{"id": "coding_review_strict", "name": "Coding Review Strict"}]
        with driver.session(database=database) as session:
            session.run(
                """
                UNWIND $profiles AS profile
                MERGE (p:FleetTaskProfile {id: profile.id})
                SET p.name = profile.name
                """,
                profiles=profiles,
            ).consume()

        barrier = Barrier(2)
        report_path = tmp_path / "fleet_loadout_report.json"

        def reconcile(tag: str) -> tuple[str, dict[str, object], bool]:
            report, plans = builder.build_report(
                [
                    _node("worker-a", f"worker-model-{tag}"),
                    _node("xwing", f"reviewer-model-{tag}"),
                    _node("worker-c", f"fallback-model-{tag}"),
                ],
                {},
                profiles,
            )
            barrier.wait(timeout=30)
            persistence = builder.persist_to_neo4j(
                driver,
                report,
                plans,
                profiles,
                database=database,
            )
            report["persistence"] = persistence
            report["publication"] = {
                "mode": "committed",
                "graph_persisted": True,
                "snapshot_id": persistence["snapshot_id"],
                "reconciliation_lock_version": persistence[
                    "reconciliation_lock_version"
                ],
            }
            published = builder.publish_committed_report(
                driver,
                report_path,
                report,
                persistence,
                database=database,
            )
            return tag, persistence, published

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(reconcile, tag) for tag in ("race-a", "race-b")]
            results = [future.result(timeout=60) for future in futures]

        ordered = sorted(results, key=lambda row: row[1]["reconciliation_lock_version"])
        first_tag, first_result, _ = ordered[0]
        final_tag, final_result, final_published = ordered[1]
        assert first_result["reconciliation_lock_version"] == 1
        assert first_result["previous_snapshot_id"] is None
        assert final_result["reconciliation_lock_version"] == 2
        assert final_result["previous_snapshot_id"] == first_result["snapshot_id"]
        assert final_published is True

        on_disk = json.loads(report_path.read_text(encoding="utf-8"))
        assert on_disk["persistence"]["snapshot_id"] == final_result["snapshot_id"]
        assert on_disk["persistence"]["reconciliation_lock_version"] == 2

        stale_report, _ = builder.build_report(
            [
                _node("worker-a", f"worker-model-{first_tag}"),
                _node("xwing", f"reviewer-model-{first_tag}"),
                _node("worker-c", f"fallback-model-{first_tag}"),
            ],
            {},
            profiles,
        )
        stale_report["persistence"] = first_result
        assert builder.publish_committed_report(
            driver,
            report_path,
            stale_report,
            first_result,
            database=database,
        ) is False
        after_stale_attempt = json.loads(report_path.read_text(encoding="utf-8"))
        assert after_stale_attempt["persistence"]["snapshot_id"] == final_result["snapshot_id"]

        with driver.session(database=database) as session:
            state = session.run(
                """
                MATCH (s:FleetSnapshot)
                WITH count(s) AS snapshots,
                     count(CASE WHEN s.current THEN 1 END) AS current_snapshots
                MATCH (lock:FleetReconciliationLock {name: 'fleet-loadout'})
                MATCH (current:FleetSnapshot {current: true})
                RETURN snapshots, current_snapshots,
                       count(lock) AS lock_count,
                       max(lock.version) AS lock_version,
                       current.snapshot_id AS current_snapshot_id
                """
            ).single(strict=True)
            assert state["snapshots"] == 2
            assert state["current_snapshots"] == 1
            assert state["lock_count"] == 1
            assert state["lock_version"] == 2
            assert state["current_snapshot_id"] == final_result["snapshot_id"]

            current_worker = session.run(
                """
                MATCH (n:FleetNodeState {node_name: 'worker-a'})
                RETURN n.loaded_models AS loaded_models
                """
            ).single(strict=True)
            assert current_worker["loaded_models"] == [f"worker-model-{final_tag}"]

            constraints = session.run(
                """
                SHOW CONSTRAINTS YIELD name
                WHERE name IN $names
                RETURN collect(name) AS names
                """,
                names=sorted(expected_constraints),
            ).single(strict=True)
            assert set(constraints["names"]) == expected_constraints
    finally:
        _clear_fleet_graph(driver, database)
        driver.close()
'''
    text = text.rstrip() + concurrent_test + "\n"
    TEST.write_text(text, encoding="utf-8")


def patch_readme() -> None:
    text = README.read_text(encoding="utf-8")
    old = '''fleet changes or drains. Reports are published atomically only after the Neo4j
transaction commits, so rejected or partial reconciliation attempts cannot
replace the last committed report or expose a partially built graph topology.
'''
    new = '''fleet changes or drains. Reconciliation creates Neo4j uniqueness constraints
for every mutable and immutable state identity, including the singleton writer
lock. Reports are published atomically under the same lock/version fence only
after the Neo4j transaction commits, so rejected, partial, concurrent, or stale
reconciliation attempts cannot replace the last committed report or expose a
partially built graph topology. The Neo4j account must have permission to create
constraints; reconciliation fails closed when those invariants cannot be enforced.
'''
    text = replace_once(text, old, new, label="README fencing guarantees")
    README.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    patch_builder()
    patch_test()
    patch_readme()
