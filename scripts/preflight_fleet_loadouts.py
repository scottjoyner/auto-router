#!/usr/bin/env python3
"""Read-only safety checks for the fleet loadout rebuild."""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from neo4j import GraphDatabase


def _single(session, query: str, **params: Any) -> dict[str, Any]:
    row = session.run(query, **params).single()
    return dict(row) if row else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight Neo4j before fleet snapshot persistence")
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD"))
    parser.add_argument("--neo4j-database", default=os.getenv("NEO4J_DATABASE", os.getenv("NEO4J_DB", "neo4j")))
    args = parser.parse_args()

    missing = [
        name
        for name, value in {
            "NEO4J_URI": args.neo4j_uri,
            "NEO4J_PASSWORD": args.neo4j_password,
        }.items()
        if not value
    ]
    if missing:
        print(f"FAIL missing required configuration: {', '.join(missing)}", file=sys.stderr)
        return 2

    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
    failures: list[str] = []
    warnings: list[str] = []
    try:
        driver.verify_connectivity()
        with driver.session(database=args.neo4j_database) as session:
            profile_count = _single(session, "MATCH (p:FleetTaskProfile) RETURN count(p) AS count").get("count", 0)
            if profile_count <= 0:
                failures.append("no FleetTaskProfile nodes found in selected database")

            duplicates = session.run(
                """
                MATCH (n:FleetNodeState)
                WITH n.snapshot_id AS snapshot_id, n.node_name AS node_name, count(*) AS copies
                WHERE snapshot_id IS NOT NULL AND node_name IS NOT NULL AND copies > 1
                RETURN snapshot_id, node_name, copies
                LIMIT 20
                """
            ).data()
            if duplicates:
                failures.append(f"duplicate snapshot/node identities exist: {duplicates}")

            model_duplicates = session.run(
                """
                MATCH (m:FleetModelState)
                WITH m.snapshot_id AS snapshot_id, m.node_name AS node_name,
                     m.model_id AS model_id, count(*) AS copies
                WHERE snapshot_id IS NOT NULL AND node_name IS NOT NULL
                  AND model_id IS NOT NULL AND copies > 1
                RETURN snapshot_id, node_name, model_id, copies
                LIMIT 20
                """
            ).data()
            if model_duplicates:
                failures.append(f"duplicate snapshot/node/model identities exist: {model_duplicates}")

            partial = session.run(
                """
                MATCH (s:FleetSnapshot)
                OPTIONAL MATCH (s)-[:HAS_NODE_STATE]->(n:FleetNodeState)
                OPTIONAL MATCH (s)-[:HAS_LOADOUT]->(l:FleetLoadout)
                WITH s, count(DISTINCT n) AS nodes, count(DISTINCT l) AS loadouts
                WHERE coalesce(s.node_count, 0) <> nodes OR coalesce(s.loadout_count, 0) <> loadouts
                RETURN s.snapshot_id AS snapshot_id, s.node_count AS expected_nodes,
                       nodes, s.loadout_count AS expected_loadouts, loadouts
                ORDER BY s.captured_at_ms DESC
                LIMIT 20
                """
            ).data()
            if partial:
                warnings.append(f"partial or legacy snapshots detected: {partial}")

            constraints = session.run("SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties RETURN name, labelsOrTypes, properties").data()
            node_ok = any(
                "FleetNodeState" in (row.get("labelsOrTypes") or [])
                and set(row.get("properties") or []) == {"snapshot_id", "node_name"}
                for row in constraints
            )
            model_ok = any(
                "FleetModelState" in (row.get("labelsOrTypes") or [])
                and set(row.get("properties") or []) == {"snapshot_id", "node_name", "model_id"}
                for row in constraints
            )
            if not node_ok:
                failures.append("missing composite uniqueness constraint for FleetNodeState(snapshot_id,node_name)")
            if not model_ok:
                failures.append("missing composite uniqueness constraint for FleetModelState(snapshot_id,node_name,model_id)")

            print(f"PASS connectivity database={args.neo4j_database} task_profiles={profile_count}")
            for warning in warnings:
                print(f"WARN {warning}")
            for failure in failures:
                print(f"FAIL {failure}", file=sys.stderr)
            return 1 if failures else 0
    finally:
        driver.close()


if __name__ == "__main__":
    raise SystemExit(main())
