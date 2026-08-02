#!/usr/bin/env python3
"""Production-oriented fleet loadout rebuild orchestration.

Adds consistent database targeting, health gates, atomic JSON reports,
post-commit verification, a stable current-snapshot pointer, and optional
snapshot retention on top of the atomic persistence implementation.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

from build_fleet_loadouts import (
    DEFAULT_REPORT_PATH,
    DEFAULT_STATS_PATH,
    PROFILE_ALIASES,
    build_report,
    load_json,
    print_report,
    probe_all_nodes,
)
from build_fleet_loadouts_atomic import persist_to_neo4j_atomic


def read_task_profiles(driver, database: str) -> list[dict[str, Any]]:
    with driver.session(database=database) as session:
        rows = session.run(
            """
            MATCH (p:FleetTaskProfile)
            RETURN properties(p) AS props
            ORDER BY p.id
            """
        ).data()
    return [row["props"] for row in rows if isinstance(row.get("props"), dict)]


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def enforce_health_gates(
    nodes,
    task_profiles: list[dict[str, Any]],
    *,
    min_nodes: int,
    min_online_nodes: int,
    min_loaded_models: int,
    min_task_profiles: int,
) -> dict[str, int]:
    node_count = len(nodes)
    online_nodes = sum(1 for node in nodes if node.online)
    loaded_models = sum(len(node.loaded_models or []) for node in nodes)
    task_profile_count = len(task_profiles)

    failures: list[str] = []
    if node_count < min_nodes:
        failures.append(f"node_count={node_count} < min_nodes={min_nodes}")
    if online_nodes < min_online_nodes:
        failures.append(f"online_nodes={online_nodes} < min_online_nodes={min_online_nodes}")
    if loaded_models < min_loaded_models:
        failures.append(f"loaded_models={loaded_models} < min_loaded_models={min_loaded_models}")
    if task_profile_count < min_task_profiles:
        failures.append(f"task_profiles={task_profile_count} < min_task_profiles={min_task_profiles}")
    if failures:
        raise RuntimeError("fleet health gate failed: " + "; ".join(failures))

    return {
        "node_count": node_count,
        "online_nodes": online_nodes,
        "loaded_models": loaded_models,
        "task_profile_count": task_profile_count,
    }


def verify_snapshot(driver, database: str, snapshot_id: str, expected: dict[str, int]) -> dict[str, Any]:
    with driver.session(database=database) as session:
        row = session.run(
            """
            MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
            OPTIONAL MATCH (s)-[:HAS_NODE_STATE]->(n:FleetNodeState)
            OPTIONAL MATCH (s)-[:HAS_MODEL_STATE]->(m:FleetModelState)
            OPTIONAL MATCH (s)-[:HAS_LOADOUT]->(l:FleetLoadout)
            OPTIONAL MATCH (l)-[:HAS_ASSIGNMENT]->(a:FleetLoadoutAssignment)
            RETURN s.persistence_status AS persistence_status,
                   count(DISTINCT n) AS node_count,
                   count(DISTINCT m) AS model_count,
                   count(DISTINCT l) AS loadout_count,
                   count(DISTINCT a) AS assignment_count
            """,
            snapshot_id=snapshot_id,
        ).single()
        if not row:
            raise RuntimeError(f"post-commit verification could not find snapshot {snapshot_id}")
        actual = dict(row)

    mismatches = [
        f"{key}: expected={value} actual={actual.get(key)}"
        for key, value in expected.items()
        if actual.get(key) != value
    ]
    if actual.get("persistence_status") != "committed":
        mismatches.append(f"persistence_status={actual.get('persistence_status')!r}")
    if mismatches:
        raise RuntimeError("post-commit verification failed: " + "; ".join(mismatches))
    return actual


def set_current_snapshot(driver, database: str, snapshot_id: str) -> None:
    with driver.session(database=database) as session:
        session.execute_write(
            lambda tx: tx.run(
                """
                MERGE (p:FleetSnapshotPointer {name: 'current'})
                SET p.snapshot_id = $snapshot_id,
                    p.updated_at = datetime()
                WITH p
                MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
                MERGE (p)-[r:POINTS_TO]->(s)
                WITH p, r, s
                MATCH (p)-[old:POINTS_TO]->(other:FleetSnapshot)
                WHERE other <> s
                DELETE old
                """,
                snapshot_id=snapshot_id,
            ).consume()
        )


def prune_snapshots(driver, database: str, keep: int) -> int:
    if keep <= 0:
        return 0
    with driver.session(database=database) as session:
        row = session.execute_write(
            lambda tx: tx.run(
                """
                MATCH (s:FleetSnapshot)
                WITH s ORDER BY s.captured_at_ms DESC
                WITH collect(s) AS snapshots
                UNWIND snapshots[$keep..] AS old
                OPTIONAL MATCH (old)-[:HAS_LOADOUT]->(l:FleetLoadout)
                OPTIONAL MATCH (l)-[:HAS_ASSIGNMENT]->(a:FleetLoadoutAssignment)
                OPTIONAL MATCH (old)-[:HAS_NODE_STATE]->(n:FleetNodeState)
                OPTIONAL MATCH (old)-[:HAS_MODEL_STATE]->(m:FleetModelState)
                OPTIONAL MATCH (old)-[:HAS_DELTA]->(d:FleetChangeDelta)
                WITH old, collect(DISTINCT l) AS ls, collect(DISTINCT a) AS assignments,
                     collect(DISTINCT n) AS ns, collect(DISTINCT m) AS ms,
                     collect(DISTINCT d) AS ds
                FOREACH (x IN assignments | DETACH DELETE x)
                FOREACH (x IN ls | DETACH DELETE x)
                FOREACH (x IN ns | DETACH DELETE x)
                FOREACH (x IN ms | DETACH DELETE x)
                FOREACH (x IN ds | DETACH DELETE x)
                DETACH DELETE old
                RETURN count(*) AS deleted
                """,
                keep=keep,
            ).single()
        )
        return int(row["deleted"] if row else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Hardened fleet loadout rebuild")
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD"))
    parser.add_argument("--neo4j-database", default=os.getenv("NEO4J_DATABASE", os.getenv("NEO4J_DB", "neo4j")))
    parser.add_argument("--stats-path", type=Path, default=DEFAULT_STATS_PATH)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-nodes", type=int, default=int(os.getenv("AUTO_ROUTER_MIN_NODES", "5")))
    parser.add_argument("--min-online-nodes", type=int, default=int(os.getenv("AUTO_ROUTER_MIN_ONLINE_NODES", "3")))
    parser.add_argument("--min-loaded-models", type=int, default=int(os.getenv("AUTO_ROUTER_MIN_LOADED_MODELS", "3")))
    parser.add_argument("--min-task-profiles", type=int, default=int(os.getenv("AUTO_ROUTER_MIN_TASK_PROFILES", "5")))
    parser.add_argument("--retain-snapshots", type=int, default=int(os.getenv("AUTO_ROUTER_RETAIN_SNAPSHOTS", "168")))
    args = parser.parse_args()

    if not args.dry_run and (not args.neo4j_uri or not args.neo4j_password):
        raise SystemExit("NEO4J_URI and NEO4J_PASSWORD are required")

    stats = load_json(args.stats_path)
    nodes = probe_all_nodes()
    driver = None

    try:
        if args.dry_run and not args.neo4j_uri:
            task_profiles = [
                {"id": value, "name": value.replace("_", " ").title()}
                for value in PROFILE_ALIASES.values()
            ]
        else:
            driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
            driver.verify_connectivity()
            task_profiles = read_task_profiles(driver, args.neo4j_database)

        health = enforce_health_gates(
            nodes,
            task_profiles,
            min_nodes=args.min_nodes,
            min_online_nodes=args.min_online_nodes,
            min_loaded_models=args.min_loaded_models,
            min_task_profiles=args.min_task_profiles,
        )
        report, plans = build_report(nodes, stats, task_profiles)
        report.update({"build_status": "planned", "graph_committed": False, "health": health})
        atomic_write_json(args.report_path, report)
        print_report(report)

        if args.dry_run:
            report["build_status"] = "dry_run_complete"
            atomic_write_json(args.report_path, report)
            return 0

        persisted = persist_to_neo4j_atomic(driver, args.neo4j_database, report, plans, task_profiles)
        expected = {
            "node_count": persisted["node_count"],
            "model_count": persisted["model_count"],
            "loadout_count": persisted["loadout_count"],
            "assignment_count": sum(
                1
                for plan in plans
                for candidate in (plan.primary, plan.reviewer, plan.fallback)
                if candidate is not None
            ),
        }
        verification = verify_snapshot(driver, args.neo4j_database, persisted["snapshot_id"], expected)
        set_current_snapshot(driver, args.neo4j_database, persisted["snapshot_id"])
        pruned = prune_snapshots(driver, args.neo4j_database, args.retain_snapshots)

        report.update(
            {
                "build_status": "committed",
                "graph_committed": True,
                "persistence": persisted,
                "verification": verification,
                "retention": {"keep": args.retain_snapshots, "deleted": pruned},
            }
        )
        atomic_write_json(args.report_path, report)
        print(f"Committed and verified snapshot {persisted['snapshot_id']}")
        return 0
    except Exception as exc:
        failure = {
            "build_status": "failed",
            "graph_committed": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        try:
            existing = load_json(args.report_path)
            existing.update(failure)
            atomic_write_json(args.report_path, existing)
        except Exception:
            pass
        raise
    finally:
        if driver is not None:
            driver.close()


if __name__ == "__main__":
    raise SystemExit(main())
