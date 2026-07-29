#!/usr/bin/env python3
"""Build and persist fleet loadouts from live fleet telemetry.

This script is a first-pass fleet rebuilder:
- probes the live LM Studio fleet
- reads the current fleet execution stats snapshot
- reads task profiles from Neo4j
- scores candidate node/model pairs for each task profile
- writes a normalized snapshot + loadout graph into Neo4j
- writes a JSON report for dashboards and humans

The goal is not perfect routing. The goal is a durable, repeatable baseline
that can be rerun every few hours and compared historically.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from auto_router.fleet_task_dispatcher import NodeInfo, probe_all_nodes

DEFAULT_STATS_PATH = Path(os.getenv("AUTO_ROUTER_FLEET_DISPATCHER_STATS_PATH", "/home/scott/git/auto-router/data/fleet_dispatcher_stats.json"))
DEFAULT_REPORT_PATH = Path(os.getenv("AUTO_ROUTER_FLEET_LOADOUT_REPORT_PATH", "/home/scott/git/auto-router/data/fleet_loadout_report.json"))
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://100.64.43.123:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "knowledge_graph_2026")
DEFAULT_NEO4J_DB = os.getenv("NEO4J_DATABASE", os.getenv("NEO4J_DB", "neo4j"))

REVIEWER_NODE_HINTS = {"x1-370", "xwing"}
REVIEWER_MODEL_HINTS = ("ornith", "orinth")
FAST_NODE_HINTS = {"beelink-ryzen-7-mini-pc", "scotts-macbook-air", "deathstar-xps-8920"}
REASONING_NODE_HINTS = {"x1-370", "xwing", "destroyer"}

PROFILE_ALIASES = {
    "coding_high_throughput": "coding_high_throughput",
    "coding_review_strict": "coding_review_strict",
    "planning_strategy": "planning_strategy",
    "summary_extraction": "summary_extraction",
    "long_context_reasoning": "long_context_reasoning",
}


@dataclass(frozen=True)
class Candidate:
    node_name: str
    model_id: str
    role: str
    score: float
    reasons: list[str]
    latency_ms: float
    loaded: bool
    online: bool
    success_rate: float
    failure_rate: float


@dataclass(frozen=True)
class LoadoutPlan:
    task_profile_id: str
    task_profile_name: str
    primary: Candidate | None
    reviewer: Candidate | None
    fallback: Candidate | None
    score: float
    rationale: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _node_stat_map(stats: dict[str, Any]) -> dict[str, dict[str, int]]:
    by_node = stats.get("stats", {}).get("by_node", {}) if isinstance(stats, dict) else {}
    mapped: dict[str, dict[str, int]] = {}
    for node_name, completed in by_node.items():
        completed_i = int(completed or 0)
        mapped[str(node_name)] = {"completed": completed_i}
    return mapped


def _slot_stats_map(stats: dict[str, Any]) -> dict[str, dict[str, int]]:
    slot_rows = stats.get("slots", []) if isinstance(stats, dict) else []
    mapped: dict[str, dict[str, int]] = {}
    for row in slot_rows:
        if not isinstance(row, dict):
            continue
        key = f"{row.get('node')}/{row.get('model')}"
        mapped[key] = {
            "completed": int(row.get("completed") or 0),
            "success": int(row.get("success") or 0),
            "failure": int(row.get("failure") or 0),
        }
    return mapped


def node_reliability(node_name: str, stats: dict[str, Any]) -> tuple[float, float]:
    node_stats = _node_stat_map(stats).get(node_name, {})
    slot_stats = _slot_stats_map(stats)
    total_success = 0
    total_failure = 0
    total_completed = 0
    for key, row in slot_stats.items():
        if not key.startswith(f"{node_name}/"):
            continue
        total_success += row.get("success", 0)
        total_failure += row.get("failure", 0)
        total_completed += row.get("completed", 0)
    total_completed = max(total_completed, node_stats.get("completed", 0))
    if total_completed <= 0:
        return 0.5, 0.5
    success_rate = total_success / max(1, total_completed)
    failure_rate = total_failure / max(1, total_completed)
    return success_rate, failure_rate


def is_reviewer(node_name: str, model_id: str) -> bool:
    node = node_name.lower()
    model = model_id.lower()
    return node in REVIEWER_NODE_HINTS or any(hint in model for hint in REVIEWER_MODEL_HINTS)


def base_node_score(node: NodeInfo, stats: dict[str, Any]) -> tuple[float, list[str], float, float]:
    success_rate, failure_rate = node_reliability(node.name, stats)
    score = 0.0
    reasons: list[str] = []
    if node.online:
        score += 30.0
        reasons.append("online")
    else:
        score -= 50.0
        reasons.append("offline")
    if node.loaded_models:
        score += 20.0
        reasons.append(f"{len(node.loaded_models)} loaded models")
    else:
        score -= 30.0
        reasons.append("no loaded models")

    if node.latency_ms and node.latency_ms > 0:
        latency_bonus = max(0.0, 20.0 - min(node.latency_ms / 50.0, 20.0))
        score += latency_bonus
        reasons.append(f"latency {node.latency_ms:.0f}ms")
    else:
        score -= 5.0
        reasons.append("no latency sample")

    if node.name.lower() in FAST_NODE_HINTS:
        score += 10.0
        reasons.append("fast-node hint")
    if node.name.lower() in REASONING_NODE_HINTS:
        score += 8.0
        reasons.append("reasoning-node hint")

    score += success_rate * 20.0
    score -= failure_rate * 15.0
    reasons.append(f"success_rate={success_rate:.2f}")
    if failure_rate:
        reasons.append(f"failure_rate={failure_rate:.2f}")
    return score, reasons, success_rate, failure_rate


def model_role_score(node: NodeInfo, model_id: str, base_score: float, profile_id: str) -> tuple[float, list[str], str]:
    reasons: list[str] = []
    score = base_score
    role = "worker"
    model_lower = model_id.lower()
    node_lower = node.name.lower()

    if is_reviewer(node.name, model_id):
        role = "reviewer"
        score += 35.0
        reasons.append("reviewer hint")
    elif node_lower in FAST_NODE_HINTS:
        score += 15.0
        reasons.append("fast worker hint")

    if "35b" in model_lower or "70b" in model_lower or "72b" in model_lower:
        score += 10.0
        reasons.append("large-model hint")
    if any(token in model_lower for token in ("coder", "code", "qwen", "deepseek")):
        score += 6.0
        reasons.append("coding-model hint")
    if any(token in model_lower for token in ("vibe", "fast", "small", "3b", "7b", "8b", "9b")):
        score += 4.0
        reasons.append("small-fast-model hint")

    if profile_id == "coding_review_strict" and role == "reviewer":
        score += 18.0
        reasons.append("strict-review boost")
    if profile_id == "summary_extraction" and role == "worker":
        score += 8.0
        reasons.append("summary worker boost")
    if profile_id == "long_context_reasoning" and "35b" in model_lower:
        score += 12.0
        reasons.append("long-context boost")

    return score, reasons, role


def build_candidates(nodes: list[NodeInfo], stats: dict[str, Any], profile_id: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    for node in nodes:
        base_score, base_reasons, success_rate, failure_rate = base_node_score(node, stats)
        for model_id in node.loaded_models:
            score, model_reasons, role = model_role_score(node, model_id, base_score, profile_id)
            # Slight penalty for online-but-empty or obviously transient nodes.
            if not node.loaded_models:
                score -= 20.0
            if profile_id == "coding_high_throughput" and role == "reviewer":
                score -= 8.0
            if profile_id == "coding_review_strict" and role == "worker" and node.name.lower() in REVIEWER_NODE_HINTS:
                score -= 10.0
            candidates.append(
                Candidate(
                    node_name=node.name,
                    model_id=model_id,
                    role=role,
                    score=round(score, 3),
                    reasons=base_reasons + model_reasons,
                    latency_ms=float(node.latency_ms or 0.0),
                    loaded=bool(model_id),
                    online=node.online,
                    success_rate=round(success_rate, 3),
                    failure_rate=round(failure_rate, 3),
                )
            )
    candidates.sort(key=lambda c: (c.score, c.success_rate, -c.failure_rate, -c.latency_ms), reverse=True)
    return candidates


def pick_best(candidates: list[Candidate], *, exclude: set[tuple[str, str]] | None = None, role: str | None = None) -> Candidate | None:
    exclude = exclude or set()
    for candidate in candidates:
        if role is not None and candidate.role != role:
            continue
        if (candidate.node_name, candidate.model_id) in exclude:
            continue
        return candidate
    return None


def plan_loadout(task_profile: dict[str, Any], nodes: list[NodeInfo], stats: dict[str, Any]) -> LoadoutPlan:
    profile_id = str(task_profile.get("id") or task_profile.get("task_profile_id") or task_profile.get("name") or "unknown")
    profile_name = str(task_profile.get("name") or profile_id)
    candidates = build_candidates(nodes, stats, profile_id)

    primary = pick_best(candidates)
    reviewer = None
    fallback = None
    rationale_parts: list[str] = []

    if profile_id == "coding_review_strict":
        primary = pick_best(candidates, role="worker") or primary
        reviewer = pick_best(candidates, exclude={(primary.node_name, primary.model_id)} if primary else set(), role="reviewer")
        fallback = pick_best(candidates, exclude={
            (primary.node_name, primary.model_id) if primary else ("", ""),
            (reviewer.node_name, reviewer.model_id) if reviewer else ("", ""),
        })
        rationale_parts.append("worker + reviewer pair")
    elif profile_id == "coding_high_throughput":
        primary = pick_best(candidates, role="worker") or primary
        reviewer = pick_best(candidates, exclude={(primary.node_name, primary.model_id)} if primary else set(), role="reviewer")
        fallback = pick_best(candidates, exclude={
            (primary.node_name, primary.model_id) if primary else ("", ""),
            (reviewer.node_name, reviewer.model_id) if reviewer else ("", ""),
        })
        rationale_parts.append("fast worker preferred")
    elif profile_id == "long_context_reasoning":
        primary = pick_best(candidates)
        reviewer = pick_best(candidates, exclude={(primary.node_name, primary.model_id)} if primary else set(), role="reviewer")
        fallback = pick_best(candidates, exclude={
            (primary.node_name, primary.model_id) if primary else ("", ""),
            (reviewer.node_name, reviewer.model_id) if reviewer else ("", ""),
        })
        rationale_parts.append("favor highest-capacity candidate")
    elif profile_id == "planning_strategy":
        primary = pick_best(candidates)
        reviewer = pick_best(candidates, exclude={(primary.node_name, primary.model_id)} if primary else set(), role="reviewer")
        fallback = pick_best(candidates, exclude={
            (primary.node_name, primary.model_id) if primary else ("", ""),
            (reviewer.node_name, reviewer.model_id) if reviewer else ("", ""),
        })
        rationale_parts.append("balanced reasoning lane")
    elif profile_id == "summary_extraction":
        primary = pick_best(candidates, role="worker") or primary
        reviewer = pick_best(candidates, exclude={(primary.node_name, primary.model_id)} if primary else set(), role="reviewer")
        fallback = pick_best(candidates, exclude={
            (primary.node_name, primary.model_id) if primary else ("", ""),
            (reviewer.node_name, reviewer.model_id) if reviewer else ("", ""),
        })
        rationale_parts.append("fast worker first")
    else:
        primary = pick_best(candidates)
        fallback = pick_best(candidates, exclude={(primary.node_name, primary.model_id)} if primary else set())
        rationale_parts.append("default highest-scoring candidate")

    score = round(sum(c.score for c in [primary, reviewer, fallback] if c), 3)
    if primary:
        rationale_parts.append(f"primary={primary.node_name}/{primary.model_id}")
    if reviewer:
        rationale_parts.append(f"reviewer={reviewer.node_name}/{reviewer.model_id}")
    if fallback:
        rationale_parts.append(f"fallback={fallback.node_name}/{fallback.model_id}")

    return LoadoutPlan(
        task_profile_id=profile_id,
        task_profile_name=profile_name,
        primary=primary,
        reviewer=reviewer,
        fallback=fallback,
        score=score,
        rationale="; ".join(rationale_parts),
    )


def read_task_profiles(driver) -> list[dict[str, Any]]:
    with driver.session(database=DEFAULT_NEO4J_DB) as session:
        rows = session.run(
            """
            MATCH (p:FleetTaskProfile)
            RETURN properties(p) AS props
            ORDER BY p.id
            """
        ).data()
    profiles: list[dict[str, Any]] = []
    for row in rows:
        props = row.get("props") if isinstance(row, dict) else None
        if isinstance(props, dict):
            profiles.append(props)
    return profiles


def _snapshot_payload(nodes: list[NodeInfo], stats: dict[str, Any], task_profiles: list[dict[str, Any]], plans: list[LoadoutPlan]) -> dict[str, Any]:
    return {
        "captured_at": utc_now().isoformat(),
        "nodes": [
            {
                "name": node.name,
                "ip": node.ip,
                "online": node.online,
                "loaded_models": node.loaded_models,
                "all_models": node.all_models,
                "latency_ms": node.latency_ms,
                "error": node.error,
                "power_watts": node.power_watts,
            }
            for node in nodes
        ],
        "stats": stats,
        "task_profiles": task_profiles,
        "loadouts": [asdict(plan) for plan in plans],
    }


def _current_snapshot_info(driver) -> dict[str, Any] | None:
    with driver.session(database=DEFAULT_NEO4J_DB) as session:
        row = session.run(
            """
            MATCH (s:FleetSnapshot)
            RETURN s.snapshot_id AS snapshot_id, s.captured_at_ms AS captured_at_ms
            ORDER BY s.captured_at_ms DESC
            LIMIT 1
            """
        ).single()
        if not row:
            return None
        return dict(row)


def persist_to_neo4j(driver, payload: dict[str, Any], plans: list[LoadoutPlan], task_profiles: list[dict[str, Any]]) -> dict[str, Any]:
    snapshot_id = str(uuid.uuid4())
    captured_at = utc_now()
    captured_at_ms = int(captured_at.timestamp() * 1000)
    previous = _current_snapshot_info(driver)
    previous_snapshot_id = str(previous["snapshot_id"]) if previous else None

    node_rows = payload.get("nodes", [])
    stats = payload.get("stats", {})

    node_by_name = {row["name"]: row for row in node_rows if isinstance(row, dict) and row.get("name")}
    model_rows: list[dict[str, Any]] = []
    for row in node_rows:
        if not isinstance(row, dict):
            continue
        for model_id in row.get("loaded_models") or []:
            model_rows.append(
                {
                    "model_id": model_id,
                    "node_name": row["name"],
                    "online": bool(row.get("online")),
                    "latency_ms": row.get("latency_ms"),
                    "loaded": True,
                }
            )

    with driver.session(database=DEFAULT_NEO4J_DB) as session:
        session.run(
            """
            MERGE (s:FleetSnapshot {snapshot_id: $snapshot_id})
            SET s.captured_at = datetime($captured_at_iso),
                s.captured_at_ms = $captured_at_ms,
                s.source = $source,
                s.node_count = $node_count,
                s.model_count = $model_count,
                s.task_profile_count = $task_profile_count,
                s.loadout_count = $loadout_count,
                s.raw_json = $raw_json,
                s.summary_json = $summary_json,
                s.previous_snapshot_id = $previous_snapshot_id,
                s.updated_at = datetime()
            """,
            {
                "snapshot_id": snapshot_id,
                "captured_at_iso": captured_at.isoformat(),
                "captured_at_ms": captured_at_ms,
                "source": "build_fleet_loadouts.py",
                "node_count": len(node_rows),
                "model_count": len(model_rows),
                "task_profile_count": len(task_profiles),
                "loadout_count": len(plans),
                "raw_json": json.dumps(payload, sort_keys=True, default=str),
                "summary_json": json.dumps(
                    {
                        "loadout_count": len(plans),
                        "online_nodes": sum(1 for row in node_rows if row.get("online")),
                        "loaded_models": len(model_rows),
                    },
                    sort_keys=True,
                ),
                "previous_snapshot_id": previous_snapshot_id,
            },
        )

        for row in node_rows:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            session.run(
                """
                MERGE (n:FleetNodeState {node_name: $node_name})
                SET n.snapshot_id = $snapshot_id,
                    n.captured_at = datetime($captured_at_iso),
                    n.online = $online,
                    n.ip = $ip,
                    n.loaded_model_count = $loaded_model_count,
                    n.loaded_models = $loaded_models,
                    n.all_models = $all_models,
                    n.latency_ms = $latency_ms,
                    n.error = $error,
                    n.power_watts = $power_watts,
                    n.raw_json = $raw_json,
                    n.updated_at = datetime()
                WITH n
                MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
                MERGE (s)-[:HAS_NODE_STATE]->(n)
                """,
                {
                    "node_name": row["name"],
                    "snapshot_id": snapshot_id,
                    "captured_at_iso": captured_at.isoformat(),
                    "online": bool(row.get("online")),
                    "ip": row.get("ip"),
                    "loaded_model_count": len(row.get("loaded_models") or []),
                    "loaded_models": row.get("loaded_models") or [],
                    "all_models": row.get("all_models") or [],
                    "latency_ms": row.get("latency_ms"),
                    "error": row.get("error"),
                    "power_watts": row.get("power_watts"),
                    "raw_json": json.dumps(row, sort_keys=True, default=str),
                },
            )

        for row in model_rows:
            state_id = f"{row['node_name']}:{row['model_id']}"
            session.run(
                """
                MERGE (m:FleetModelState {state_id: $state_id})
                SET m.model_id = $model_id,
                    m.snapshot_id = $snapshot_id,
                    m.captured_at = datetime($captured_at_iso),
                    m.node_name = $node_name,
                    m.online = $online,
                    m.loaded = $loaded,
                    m.latency_ms = $latency_ms,
                    m.raw_json = $raw_json,
                    m.updated_at = datetime()
                WITH m
                MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
                MERGE (s)-[:HAS_MODEL_STATE]->(m)
                WITH m
                MATCH (n:FleetNodeState {node_name: $node_name})
                MERGE (n)-[:HOSTS_MODEL]->(m)
                """,
                {
                    "snapshot_id": snapshot_id,
                    "state_id": state_id,
                    "captured_at_iso": captured_at.isoformat(),
                    "model_id": row["model_id"],
                    "node_name": row["node_name"],
                    "online": bool(row.get("online")),
                    "loaded": bool(row.get("loaded")),
                    "latency_ms": row.get("latency_ms"),
                    "raw_json": json.dumps(row, sort_keys=True, default=str),
                },
            )

        for plan in plans:
            loadout_id = f"{snapshot_id}:{plan.task_profile_id}"
            session.run(
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
                {
                    "loadout_id": loadout_id,
                    "snapshot_id": snapshot_id,
                    "task_profile_id": plan.task_profile_id,
                    "task_profile_name": plan.task_profile_name,
                    "score": plan.score,
                    "rationale": plan.rationale,
                    "primary_node": plan.primary.node_name if plan.primary else None,
                    "primary_model": plan.primary.model_id if plan.primary else None,
                    "reviewer_node": plan.reviewer.node_name if plan.reviewer else None,
                    "reviewer_model": plan.reviewer.model_id if plan.reviewer else None,
                    "fallback_node": plan.fallback.node_name if plan.fallback else None,
                    "fallback_model": plan.fallback.model_id if plan.fallback else None,
                },
            )

            for rank, (slot_name, candidate) in enumerate(
                (
                    ("primary", plan.primary),
                    ("reviewer", plan.reviewer),
                    ("fallback", plan.fallback),
                ),
                start=1,
            ):
                if candidate is None:
                    continue
                assignment_id = f"{loadout_id}:{slot_name}"
                session.run(
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
                    MATCH (n:FleetNodeState {node_name: $node_name})
                    MATCH (m:FleetModelState {state_id: $model_state_id})
                    MERGE (l)-[:HAS_ASSIGNMENT]->(a)
                    MERGE (a)-[:USES_NODE]->(n)
                    MERGE (a)-[:USES_MODEL]->(m)
                    """,
                    {
                        "assignment_id": assignment_id,
                        "loadout_id": loadout_id,
                        "snapshot_id": snapshot_id,
                        "task_profile_id": plan.task_profile_id,
                        "slot_name": slot_name,
                        "rank": rank,
                        "node_name": candidate.node_name,
                        "model_id": candidate.model_id,
                        "model_state_id": (
                            f"{candidate.node_name}:{candidate.model_id}"
                        ),
                        "role": candidate.role,
                        "score": candidate.score,
                        "reasons": candidate.reasons,
                    },
                )

        delta_summary = {
            "snapshot_id": snapshot_id,
            "previous_snapshot_id": previous_snapshot_id,
            "loadout_count": len(plans),
            "changed_loadouts": [],
        }
        if previous_snapshot_id:
            # First pass delta is intentionally lightweight: we record the baseline relationship
            # and defer deeper diffing until we have multiple persisted snapshots.
            delta_summary["changed_loadouts"] = [plan.task_profile_id for plan in plans]

        session.run(
            """
            MERGE (d:FleetChangeDelta {delta_id: $delta_id})
            SET d.snapshot_id = $snapshot_id,
                d.previous_snapshot_id = $previous_snapshot_id,
                d.delta_json = $delta_json,
                d.updated_at = datetime()
            WITH d
            MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
            MERGE (s)-[:HAS_DELTA]->(d)
            """,
            {
                "delta_id": snapshot_id,
                "snapshot_id": snapshot_id,
                "previous_snapshot_id": previous_snapshot_id,
                "delta_json": json.dumps(delta_summary, sort_keys=True),
            },
        )

        # Remove states absent from the completed snapshot only after all new
        # state has been written. This avoids an empty topology window and keeps
        # same-model deployments on different nodes distinct.
        session.run(
            """
            MATCH (n:FleetNodeState)
            WHERE n.snapshot_id IS NULL OR n.snapshot_id <> $snapshot_id
            DETACH DELETE n
            """,
            {"snapshot_id": snapshot_id},
        )
        session.run(
            """
            MATCH (m:FleetModelState)
            WHERE m.snapshot_id IS NULL OR m.snapshot_id <> $snapshot_id
            DETACH DELETE m
            """,
            {"snapshot_id": snapshot_id},
        )

    return {
        "snapshot_id": snapshot_id,
        "captured_at": captured_at.isoformat(),
        "previous_snapshot_id": previous_snapshot_id,
        "node_count": len(node_rows),
        "model_count": len(model_rows),
        "loadout_count": len(plans),
        "task_profile_count": len(task_profiles),
        "delta": delta_summary,
    }


def build_report(nodes: list[NodeInfo], stats: dict[str, Any], task_profiles: list[dict[str, Any]]) -> tuple[dict[str, Any], list[LoadoutPlan]]:
    plans = [plan_loadout(profile, nodes, stats) for profile in task_profiles]
    plans = sorted(plans, key=lambda plan: plan.score, reverse=True)
    return _snapshot_payload(nodes, stats, task_profiles, plans), plans


def print_report(report: dict[str, Any]) -> None:
    print(f"Snapshot: {report['captured_at']}")
    print(f"Nodes: {len(report.get('nodes', []))} | Task profiles: {len(report.get('task_profiles', []))} | Loadouts: {len(report.get('loadouts', []))}")
    print("")
    for loadout in report.get("loadouts", []):
        primary = loadout.get("primary") or {}
        reviewer = loadout.get("reviewer") or {}
        fallback = loadout.get("fallback") or {}
        print(f"- {loadout.get('task_profile_id')} -> score {loadout.get('score')} | {loadout.get('rationale')}")
        if primary:
            print(f"  primary:  {primary.get('node_name')}/{primary.get('model_id')} ({primary.get('role')}, score={primary.get('score')})")
        if reviewer:
            print(f"  reviewer: {reviewer.get('node_name')}/{reviewer.get('model_id')} ({reviewer.get('role')}, score={reviewer.get('score')})")
        if fallback:
            print(f"  fallback: {fallback.get('node_name')}/{fallback.get('model_id')} ({fallback.get('role')}, score={fallback.get('score')})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build fleet loadouts and persist them to Neo4j")
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI)
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER)
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD)
    parser.add_argument("--neo4j-database", default=DEFAULT_NEO4J_DB)
    parser.add_argument("--stats-path", type=Path, default=DEFAULT_STATS_PATH)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--dry-run", action="store_true", help="Do not write to Neo4j; just print and save the JSON report")
    args = parser.parse_args()

    stats = load_json(args.stats_path)
    nodes = probe_all_nodes()

    driver = None
    try:
        if not args.dry_run:
            driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
            task_profiles = read_task_profiles(driver)
        else:
            task_profiles = [
                {"id": value, "name": value.replace("_", " ").title()}
                for value in PROFILE_ALIASES.values()
            ]

        report, plans = build_report(nodes, stats, task_profiles)
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print_report(report)

        if args.dry_run:
            return 0

        persist_result = persist_to_neo4j(driver, report, plans, task_profiles)
        print("")
        print(f"Persisted snapshot {persist_result['snapshot_id']} with {persist_result['loadout_count']} loadouts")
        if persist_result.get("previous_snapshot_id"):
            print(f"Previous snapshot: {persist_result['previous_snapshot_id']}")
        return 0
    finally:
        if driver is not None:
            driver.close()


if __name__ == "__main__":
    raise SystemExit(main())
