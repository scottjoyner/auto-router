#!/usr/bin/env python3
"""Build fleet loadouts with snapshot-scoped, atomic Neo4j persistence.

This is a drop-in replacement for ``build_fleet_loadouts.py`` while the
legacy writer remains available for comparison. All graph writes for a
snapshot occur in one managed transaction. Fleet node and model state are
immutable historical records scoped to the snapshot that captured them.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from neo4j import GraphDatabase

from build_fleet_loadouts import (
    DEFAULT_NEO4J_DB,
    DEFAULT_NEO4J_PASSWORD,
    DEFAULT_NEO4J_URI,
    DEFAULT_NEO4J_USER,
    DEFAULT_REPORT_PATH,
    DEFAULT_STATS_PATH,
    PROFILE_ALIASES,
    LoadoutPlan,
    build_report,
    load_json,
    print_report,
    probe_all_nodes,
    read_task_profiles,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _current_snapshot_info(driver, database: str) -> dict[str, Any] | None:
    with driver.session(database=database) as session:
        row = session.run(
            """
            MATCH (s:FleetSnapshot)
            RETURN s.snapshot_id AS snapshot_id, s.captured_at_ms AS captured_at_ms
            ORDER BY s.captured_at_ms DESC
            LIMIT 1
            """
        ).single()
        return dict(row) if row else None


def _persist_snapshot_tx(
    tx,
    *,
    snapshot_id: str,
    captured_at_iso: str,
    captured_at_ms: int,
    previous_snapshot_id: str | None,
    payload: dict[str, Any],
    plans: list[LoadoutPlan],
    task_profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    node_rows = [row for row in payload.get("nodes", []) if isinstance(row, dict) and row.get("name")]
    model_rows = [
        {
            "snapshot_id": snapshot_id,
            "node_name": row["name"],
            "model_id": model_id,
            "online": bool(row.get("online")),
            "loaded": True,
            "latency_ms": row.get("latency_ms"),
            "raw_json": json.dumps(
                {
                    "node_name": row["name"],
                    "model_id": model_id,
                    "online": bool(row.get("online")),
                    "loaded": True,
                    "latency_ms": row.get("latency_ms"),
                },
                sort_keys=True,
                default=str,
            ),
        }
        for row in node_rows
        for model_id in (row.get("loaded_models") or [])
    ]

    tx.run(
        """
        MERGE (s:FleetSnapshot {snapshot_id: $snapshot_id})
        SET s.captured_at = datetime($captured_at_iso),
            s.captured_at_ms = $captured_at_ms,
            s.source = 'build_fleet_loadouts_atomic.py',
            s.node_count = $node_count,
            s.model_count = $model_count,
            s.task_profile_count = $task_profile_count,
            s.loadout_count = $loadout_count,
            s.raw_json = $raw_json,
            s.summary_json = $summary_json,
            s.previous_snapshot_id = $previous_snapshot_id,
            s.persistence_status = 'committed',
            s.updated_at = datetime()
        """,
        snapshot_id=snapshot_id,
        captured_at_iso=captured_at_iso,
        captured_at_ms=captured_at_ms,
        node_count=len(node_rows),
        model_count=len(model_rows),
        task_profile_count=len(task_profiles),
        loadout_count=len(plans),
        raw_json=json.dumps(payload, sort_keys=True, default=str),
        summary_json=json.dumps(
            {
                "loadout_count": len(plans),
                "online_nodes": sum(1 for row in node_rows if row.get("online")),
                "loaded_models": len(model_rows),
            },
            sort_keys=True,
        ),
        previous_snapshot_id=previous_snapshot_id,
    )

    tx.run(
        """
        UNWIND $rows AS row
        MERGE (n:FleetNodeState {
            snapshot_id: $snapshot_id,
            node_name: row.name
        })
        SET n.captured_at = datetime($captured_at_iso),
            n.online = row.online,
            n.ip = row.ip,
            n.loaded_model_count = size(row.loaded_models),
            n.loaded_models = row.loaded_models,
            n.all_models = row.all_models,
            n.latency_ms = row.latency_ms,
            n.error = row.error,
            n.power_watts = row.power_watts,
            n.raw_json = row.raw_json,
            n.updated_at = datetime()
        WITH n
        MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
        MERGE (s)-[:HAS_NODE_STATE]->(n)
        """,
        snapshot_id=snapshot_id,
        captured_at_iso=captured_at_iso,
        rows=[
            {
                "name": row["name"],
                "online": bool(row.get("online")),
                "ip": row.get("ip"),
                "loaded_models": row.get("loaded_models") or [],
                "all_models": row.get("all_models") or [],
                "latency_ms": row.get("latency_ms"),
                "error": row.get("error"),
                "power_watts": row.get("power_watts"),
                "raw_json": json.dumps(row, sort_keys=True, default=str),
            }
            for row in node_rows
        ],
    )

    tx.run(
        """
        UNWIND $rows AS row
        MERGE (m:FleetModelState {
            snapshot_id: $snapshot_id,
            node_name: row.node_name,
            model_id: row.model_id
        })
        SET m.state_id = $snapshot_id + ':' + row.node_name + ':' + row.model_id,
            m.captured_at = datetime($captured_at_iso),
            m.online = row.online,
            m.loaded = row.loaded,
            m.latency_ms = row.latency_ms,
            m.raw_json = row.raw_json,
            m.updated_at = datetime()
        WITH m, row
        MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
        MATCH (n:FleetNodeState {
            snapshot_id: $snapshot_id,
            node_name: row.node_name
        })
        MERGE (s)-[:HAS_MODEL_STATE]->(m)
        MERGE (n)-[:HOSTS_MODEL]->(m)
        """,
        snapshot_id=snapshot_id,
        captured_at_iso=captured_at_iso,
        rows=model_rows,
    )

    for plan in plans:
        loadout_id = f"{snapshot_id}:{plan.task_profile_id}"
        tx.run(
            """
            MERGE (l:FleetLoadout {loadout_id: $loadout_id})
            SET l.snapshot_id = $snapshot_id,
                l.task_profile_id = $task_profile_id,
                l.task_profile_name = $task_profile_name,
                l.score = $score,
                l.rationale = $rationale,
                l.primary_node = $primary_node,
                l.primary_model = $primary_model,
                l.reviewer_node = $reviewer_node,
                l.reviewer_model = $reviewer_model,
                l.fallback_node = $fallback_node,
                l.fallback_model = $fallback_model,
                l.updated_at = datetime()
            WITH l
            MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
            MATCH (p:FleetTaskProfile {id: $task_profile_id})
            MERGE (s)-[:HAS_LOADOUT]->(l)
            MERGE (l)-[:FOR_PROFILE]->(p)
            """,
            loadout_id=loadout_id,
            snapshot_id=snapshot_id,
            task_profile_id=plan.task_profile_id,
            task_profile_name=plan.task_profile_name,
            score=plan.score,
            rationale=plan.rationale,
            primary_node=plan.primary.node_name if plan.primary else None,
            primary_model=plan.primary.model_id if plan.primary else None,
            reviewer_node=plan.reviewer.node_name if plan.reviewer else None,
            reviewer_model=plan.reviewer.model_id if plan.reviewer else None,
            fallback_node=plan.fallback.node_name if plan.fallback else None,
            fallback_model=plan.fallback.model_id if plan.fallback else None,
        )

        for rank, (slot_name, candidate) in enumerate(
            (("primary", plan.primary), ("reviewer", plan.reviewer), ("fallback", plan.fallback)),
            start=1,
        ):
            if candidate is None:
                continue
            assignment_id = f"{loadout_id}:{slot_name}"
            tx.run(
                """
                MERGE (a:FleetLoadoutAssignment {assignment_id: $assignment_id})
                SET a.loadout_id = $loadout_id,
                    a.snapshot_id = $snapshot_id,
                    a.task_profile_id = $task_profile_id,
                    a.slot_name = $slot_name,
                    a.rank = $rank,
                    a.node_name = $node_name,
                    a.model_id = $model_id,
                    a.role = $role,
                    a.score = $score,
                    a.reasons = $reasons,
                    a.updated_at = datetime()
                WITH a
                MATCH (l:FleetLoadout {loadout_id: $loadout_id})
                MATCH (n:FleetNodeState {
                    snapshot_id: $snapshot_id,
                    node_name: $node_name
                })
                MATCH (m:FleetModelState {
                    snapshot_id: $snapshot_id,
                    node_name: $node_name,
                    model_id: $model_id
                })
                MERGE (l)-[:HAS_ASSIGNMENT]->(a)
                MERGE (a)-[:USES_NODE]->(n)
                MERGE (a)-[:USES_MODEL]->(m)
                """,
                assignment_id=assignment_id,
                loadout_id=loadout_id,
                snapshot_id=snapshot_id,
                task_profile_id=plan.task_profile_id,
                slot_name=slot_name,
                rank=rank,
                node_name=candidate.node_name,
                model_id=candidate.model_id,
                role=candidate.role,
                score=candidate.score,
                reasons=candidate.reasons,
            )

    delta = {
        "snapshot_id": snapshot_id,
        "previous_snapshot_id": previous_snapshot_id,
        "loadout_count": len(plans),
        "changed_loadouts": [plan.task_profile_id for plan in plans] if previous_snapshot_id else [],
    }
    tx.run(
        """
        MERGE (d:FleetChangeDelta {delta_id: $snapshot_id})
        SET d.snapshot_id = $snapshot_id,
            d.previous_snapshot_id = $previous_snapshot_id,
            d.delta_json = $delta_json,
            d.updated_at = datetime()
        WITH d
        MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
        MERGE (s)-[:HAS_DELTA]->(d)
        """,
        snapshot_id=snapshot_id,
        previous_snapshot_id=previous_snapshot_id,
        delta_json=json.dumps(delta, sort_keys=True),
    )

    return {
        "snapshot_id": snapshot_id,
        "captured_at": captured_at_iso,
        "previous_snapshot_id": previous_snapshot_id,
        "node_count": len(node_rows),
        "model_count": len(model_rows),
        "loadout_count": len(plans),
        "task_profile_count": len(task_profiles),
        "delta": delta,
    }


def persist_to_neo4j_atomic(
    driver,
    database: str,
    payload: dict[str, Any],
    plans: list[LoadoutPlan],
    task_profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    captured_at = _utc_now()
    snapshot_id = str(uuid4())
    previous = _current_snapshot_info(driver, database)
    previous_snapshot_id = str(previous["snapshot_id"]) if previous else None

    with driver.session(database=database) as session:
        return session.execute_write(
            _persist_snapshot_tx,
            snapshot_id=snapshot_id,
            captured_at_iso=captured_at.isoformat(),
            captured_at_ms=int(captured_at.timestamp() * 1000),
            previous_snapshot_id=previous_snapshot_id,
            payload=payload,
            plans=plans,
            task_profiles=task_profiles,
        )


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and atomically persist fleet loadouts")
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI)
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER)
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD)
    parser.add_argument("--neo4j-database", default=DEFAULT_NEO4J_DB)
    parser.add_argument("--stats-path", type=Path, default=DEFAULT_STATS_PATH)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stats = load_json(args.stats_path)
    nodes = probe_all_nodes()
    driver = None

    try:
        if args.dry_run:
            task_profiles = [
                {"id": value, "name": value.replace("_", " ").title()}
                for value in PROFILE_ALIASES.values()
            ]
        else:
            driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
            task_profiles = read_task_profiles(driver)

        report, plans = build_report(nodes, stats, task_profiles)
        report["build_status"] = "planned"
        report["graph_committed"] = False
        _write_report(args.report_path, report)
        print_report(report)

        if args.dry_run:
            report["build_status"] = "dry_run_complete"
            _write_report(args.report_path, report)
            return 0

        try:
            persisted = persist_to_neo4j_atomic(driver, args.neo4j_database, report, plans, task_profiles)
        except Exception as exc:
            report["build_status"] = "persistence_failed"
            report["graph_committed"] = False
            report["error_type"] = type(exc).__name__
            report["error"] = str(exc)
            _write_report(args.report_path, report)
            raise

        report["build_status"] = "committed"
        report["graph_committed"] = True
        report["persistence"] = persisted
        _write_report(args.report_path, report)
        print(f"Persisted snapshot {persisted['snapshot_id']} with {persisted['loadout_count']} loadouts")
        return 0
    finally:
        if driver is not None:
            driver.close()


if __name__ == "__main__":
    raise SystemExit(main())
