from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest
from neo4j import GraphDatabase

from auto_router.fleet_task_dispatcher import NodeInfo


pytestmark = pytest.mark.skipif(
    os.getenv("NEO4J_INTEGRATION") != "1",
    reason="requires the dedicated ephemeral Neo4j workflow",
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_fleet_loadouts.py"
SPEC = importlib.util.spec_from_file_location(
    "fleet_loadout_builder_neo4j_integration",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


def _node(name: str, model_id: str) -> NodeInfo:
    return NodeInfo(
        name=name,
        ip="127.0.0.1",
        online=True,
        loaded_models=[model_id],
        all_models=[model_id],
        configured_models=[model_id],
        latency_ms=10.0,
        endpoint_status={"api/v1/models": "ok:1"},
        base_url="http://127.0.0.1:1234",
        provider_name=name,
        discovery_source="live",
        inventory_complete=True,
        inventory_authoritative=True,
        loaded_state_source="native",
        observed_at="2026-08-04T18:00:00+00:00",
    )


def _clear_fleet_graph(driver, database: str) -> None:
    with driver.session(database=database) as session:
        session.run(
            """
            MATCH (n)
            WHERE n:FleetSnapshot
               OR n:FleetNodeState
               OR n:FleetNodeObservation
               OR n:FleetModelState
               OR n:FleetModelObservation
               OR n:FleetLoadout
               OR n:FleetLoadoutAssignment
               OR n:FleetChangeDelta
               OR n:FleetTaskProfile
            DETACH DELETE n
            """
        ).consume()


def test_two_reconciliations_preserve_history_and_current_state() -> None:
    uri = os.environ["NEO4J_URI"]
    user = os.environ["NEO4J_USER"]
    password = os.environ["NEO4J_PASSWORD"]
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        _clear_fleet_graph(driver, database)
        profiles = [
            {
                "id": "coding_review_strict",
                "name": "Coding Review Strict",
            }
        ]
        with driver.session(database=database) as session:
            session.run(
                """
                UNWIND $profiles AS profile
                MERGE (p:FleetTaskProfile {id: profile.id})
                SET p.name = profile.name
                """,
                profiles=profiles,
            ).consume()

        first_report, first_plans = builder.build_report(
            [
                _node("worker-a", "worker-model-v1"),
                _node("xwing", "reviewer-model-v1"),
                _node("worker-c", "fallback-model-v1"),
            ],
            {},
            profiles,
        )
        first = builder.persist_to_neo4j(
            driver,
            first_report,
            first_plans,
            profiles,
            database=database,
        )

        second_report, second_plans = builder.build_report(
            [
                _node("worker-a", "worker-model-v2"),
                _node("xwing", "reviewer-model-v2"),
                _node("worker-c", "fallback-model-v2"),
            ],
            {},
            profiles,
        )
        second = builder.persist_to_neo4j(
            driver,
            second_report,
            second_plans,
            profiles,
            database=database,
        )

        assert second["previous_snapshot_id"] == first["snapshot_id"]
        with driver.session(database=database) as session:
            snapshot_counts = session.run(
                """
                MATCH (s:FleetSnapshot)
                RETURN count(s) AS snapshots,
                       count(CASE WHEN s.current THEN 1 END) AS current_snapshots,
                       collect(s.status) AS statuses
                """
            ).single(strict=True)
            assert snapshot_counts["snapshots"] == 2
            assert snapshot_counts["current_snapshots"] == 1
            assert set(snapshot_counts["statuses"]) == {"ready"}

            current_models = session.run(
                """
                MATCH (n:FleetNodeState)
                RETURN n.node_name AS node_name, n.loaded_models AS loaded_models
                ORDER BY node_name
                """
            ).data()
            assert current_models == [
                {"node_name": "worker-a", "loaded_models": ["worker-model-v2"]},
                {"node_name": "worker-c", "loaded_models": ["fallback-model-v2"]},
                {"node_name": "xwing", "loaded_models": ["reviewer-model-v2"]},
            ]

            first_observation = session.run(
                """
                MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
                      -[:HAS_NODE_OBSERVATION]->
                      (o:FleetNodeObservation {node_name: 'worker-a'})
                RETURN o.loaded_models AS loaded_models,
                       o.inventory_authoritative AS authoritative,
                       o.loaded_state_source AS loaded_state_source
                """,
                snapshot_id=first["snapshot_id"],
            ).single(strict=True)
            assert first_observation["loaded_models"] == ["worker-model-v1"]
            assert first_observation["authoritative"] is True
            assert first_observation["loaded_state_source"] == "native"

            immutable_counts = session.run(
                """
                MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
                OPTIONAL MATCH (s)-[:HAS_NODE_OBSERVATION]->(node_observation)
                WITH s, count(node_observation) AS node_observations
                OPTIONAL MATCH (s)-[:HAS_MODEL_OBSERVATION]->(model_observation)
                RETURN node_observations,
                       count(model_observation) AS model_observations
                """,
                snapshot_id=first["snapshot_id"],
            ).single(strict=True)
            assert immutable_counts["node_observations"] == 3
            assert immutable_counts["model_observations"] == 3

            assignment_links = session.run(
                """
                MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
                      -[:HAS_LOADOUT]->(:FleetLoadout)
                      -[:HAS_ASSIGNMENT]->(a:FleetLoadoutAssignment)
                OPTIONAL MATCH (a)-[:USES_NODE_OBSERVATION]->(node_observation)
                OPTIONAL MATCH (a)-[:USES_MODEL_OBSERVATION]->(model_observation)
                RETURN count(DISTINCT a) AS assignments,
                       count(DISTINCT node_observation) AS node_links,
                       count(DISTINCT model_observation) AS model_links
                """,
                snapshot_id=first["snapshot_id"],
            ).single(strict=True)
            assert assignment_links["assignments"] >= 2
            assert assignment_links["node_links"] == assignment_links["assignments"]
            assert assignment_links["model_links"] == assignment_links["assignments"]

            mutable_history_links = session.run(
                """
                MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
                OPTIONAL MATCH (s)-[r:HAS_NODE_STATE|HAS_MODEL_STATE]->()
                RETURN count(r) AS mutable_links
                """,
                snapshot_id=first["snapshot_id"],
            ).single(strict=True)
            assert mutable_history_links["mutable_links"] == 0

            before_rejection = session.run(
                "MATCH (s:FleetSnapshot) RETURN count(s) AS count"
            ).single(strict=True)["count"]

        with pytest.raises(builder.UnsafeFleetSnapshotError):
            builder.persist_to_neo4j(
                driver,
                {"nodes": [], "summary": {}},
                [],
                profiles,
                database=database,
            )

        with driver.session(database=database) as session:
            after_rejection = session.run(
                "MATCH (s:FleetSnapshot) RETURN count(s) AS count"
            ).single(strict=True)["count"]
            assert after_rejection == before_rejection
    finally:
        _clear_fleet_graph(driver, database)
        driver.close()
