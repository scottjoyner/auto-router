#!/usr/bin/env python3
"""Explicit, guarded retention for historical fleet snapshots."""
from __future__ import annotations

import argparse
import os
from typing import Any

from neo4j import GraphDatabase


def candidates(session, keep: int) -> list[dict[str, Any]]:
    return session.run(
        """
        MATCH (s:FleetSnapshot)
        WITH s ORDER BY s.captured_at_ms DESC
        WITH collect(s) AS snapshots
        UNWIND snapshots[$keep..] AS old
        RETURN old.snapshot_id AS snapshot_id,
               old.captured_at AS captured_at,
               old.node_count AS node_count,
               old.loadout_count AS loadout_count
        ORDER BY old.captured_at_ms ASC
        """,
        keep=keep,
    ).data()


def delete_one(tx, snapshot_id: str) -> None:
    tx.run(
        """
        MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
        OPTIONAL MATCH (s)-[:HAS_LOADOUT]->(l:FleetLoadout)
        OPTIONAL MATCH (l)-[:HAS_ASSIGNMENT]->(a:FleetLoadoutAssignment)
        OPTIONAL MATCH (s)-[:HAS_NODE_STATE]->(n:FleetNodeState)
        OPTIONAL MATCH (s)-[:HAS_MODEL_STATE]->(m:FleetModelState)
        OPTIONAL MATCH (s)-[:HAS_DELTA]->(d:FleetChangeDelta)
        WITH s,
             [x IN collect(DISTINCT a) WHERE x IS NOT NULL] AS assignments,
             [x IN collect(DISTINCT l) WHERE x IS NOT NULL] AS loadouts,
             [x IN collect(DISTINCT n) WHERE x IS NOT NULL] AS nodes,
             [x IN collect(DISTINCT m) WHERE x IS NOT NULL] AS models,
             [x IN collect(DISTINCT d) WHERE x IS NOT NULL] AS deltas
        FOREACH (x IN assignments | DETACH DELETE x)
        FOREACH (x IN loadouts | DETACH DELETE x)
        FOREACH (x IN nodes | DETACH DELETE x)
        FOREACH (x IN models | DETACH DELETE x)
        FOREACH (x IN deltas | DETACH DELETE x)
        DETACH DELETE s
        """,
        snapshot_id=snapshot_id,
    ).consume()


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune old fleet snapshots")
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI"), required=False)
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD"), required=False)
    parser.add_argument("--neo4j-database", default=os.getenv("NEO4J_DATABASE", os.getenv("NEO4J_DB", "neo4j")))
    parser.add_argument("--keep", type=int, required=True)
    parser.add_argument("--apply", action="store_true", help="Actually delete; otherwise only list candidates")
    args = parser.parse_args()

    if args.keep < 1:
        raise SystemExit("--keep must be at least 1")
    if not args.neo4j_uri or not args.neo4j_password:
        raise SystemExit("NEO4J_URI and NEO4J_PASSWORD are required")

    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
    try:
        driver.verify_connectivity()
        with driver.session(database=args.neo4j_database) as session:
            rows = candidates(session, args.keep)
            for row in rows:
                print(row)
            print(f"candidates={len(rows)} keep={args.keep} apply={args.apply}")
            if not args.apply:
                return 0
            for row in rows:
                session.execute_write(delete_one, row["snapshot_id"])
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    raise SystemExit(main())
