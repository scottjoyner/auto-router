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
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from auto_router.fleet_task_dispatcher import NodeInfo, probe_all_nodes

DEFAULT_STATS_PATH = Path(
    os.getenv(
        "AUTO_ROUTER_FLEET_DISPATCHER_STATS_PATH",
        str(REPO_ROOT / "data" / "fleet_dispatcher_stats.json"),
    )
)
DEFAULT_REPORT_PATH = Path(
    os.getenv(
        "AUTO_ROUTER_FLEET_LOADOUT_REPORT_PATH",
        str(REPO_ROOT / "data" / "fleet_loadout_report.json"),
    )
)
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://100.64.43.123:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
DEFAULT_NEO4J_DB = os.getenv("NEO4J_DATABASE", os.getenv("NEO4J_DB", "neo4j"))
DEFAULT_MAX_FLEET_DROP_FRACTION = float(os.getenv("AUTO_ROUTER_MAX_FLEET_DROP_FRACTION", "0.5"))
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


class UnsafeFleetSnapshotError(RuntimeError):
    """Raised when reconciliation would replace useful state with an unsafe snapshot."""


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


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Publish JSON with a same-filesystem atomic replace.

    Readers observe either the previous complete report or the next complete
    report, never a truncated file from a crash or interrupted write.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


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


def model_role_score(
    node: NodeInfo, model_id: str, base_score: float, profile_id: str
) -> tuple[float, list[str], str]:
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


def build_candidates(
    nodes: list[NodeInfo], stats: dict[str, Any], profile_id: str
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for node in nodes:
        # Defense in depth: only native, explicit loaded-state inventory may
        # become a routing candidate. Cached and compatibility-only observations
        # remain useful diagnostics but are never executable assignments.
        if not node.online or not node.inventory_authoritative:
            continue
        base_score, base_reasons, success_rate, failure_rate = base_node_score(node, stats)
        for model_id in sorted(set(node.loaded_models)):
            score, model_reasons, role = model_role_score(node, model_id, base_score, profile_id)
            # Slight penalty for online-but-empty or obviously transient nodes.
            if not node.loaded_models:
                score -= 20.0
            if profile_id == "coding_high_throughput" and role == "reviewer":
                score -= 8.0
            if (
                profile_id == "coding_review_strict"
                and role == "worker"
                and node.name.lower() in REVIEWER_NODE_HINTS
            ):
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
    candidates.sort(
        key=lambda c: (c.score, c.success_rate, -c.failure_rate, -c.latency_ms), reverse=True
    )
    return candidates


def pick_best(
    candidates: list[Candidate],
    *,
    exclude: set[tuple[str, str]] | None = None,
    exclude_nodes: set[str] | None = None,
    role: str | None = None,
) -> Candidate | None:
    exclude = exclude or set()
    exclude_nodes = exclude_nodes or set()
    for candidate in candidates:
        if role is not None and candidate.role != role:
            continue
        if candidate.node_name in exclude_nodes:
            continue
        if (candidate.node_name, candidate.model_id) in exclude:
            continue
        return candidate
    return None


def pick_resilient(
    candidates: list[Candidate],
    selected: list[Candidate | None],
    *,
    role: str | None = None,
) -> Candidate | None:
    """Prefer a different physical node, then relax only to preserve availability."""

    selected_candidates = [candidate for candidate in selected if candidate is not None]
    excluded_pairs = {
        (candidate.node_name, candidate.model_id) for candidate in selected_candidates
    }
    excluded_nodes = {candidate.node_name for candidate in selected_candidates}
    return pick_best(
        candidates,
        exclude=excluded_pairs,
        exclude_nodes=excluded_nodes,
        role=role,
    ) or pick_best(candidates, exclude=excluded_pairs, role=role)


def plan_loadout(
    task_profile: dict[str, Any],
    nodes: list[NodeInfo],
    stats: dict[str, Any],
) -> LoadoutPlan:
    profile_id = str(
        task_profile.get("id")
        or task_profile.get("task_profile_id")
        or task_profile.get("name")
        or "unknown"
    )
    profile_name = str(task_profile.get("name") or profile_id)
    candidates = build_candidates(nodes, stats, profile_id)

    worker_first = {
        "coding_review_strict",
        "coding_high_throughput",
        "summary_extraction",
    }
    reviewer_profiles = {
        "coding_review_strict",
        "coding_high_throughput",
        "long_context_reasoning",
        "planning_strategy",
        "summary_extraction",
    }
    rationale_by_profile = {
        "coding_review_strict": "worker + reviewer pair",
        "coding_high_throughput": "fast worker preferred",
        "long_context_reasoning": "favor highest-capacity candidate",
        "planning_strategy": "balanced reasoning lane",
        "summary_extraction": "fast worker first",
    }

    primary = (
        pick_best(candidates, role="worker") or pick_best(candidates)
        if profile_id in worker_first
        else pick_best(candidates)
    )
    reviewer = (
        pick_resilient(candidates, [primary], role="reviewer")
        if profile_id in reviewer_profiles
        else None
    )
    fallback = pick_resilient(candidates, [primary, reviewer])

    rationale_parts = [rationale_by_profile.get(profile_id, "default highest-scoring candidate")]
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


def read_task_profiles(
    driver,
    *,
    database: str = DEFAULT_NEO4J_DB,
) -> list[dict[str, Any]]:
    with driver.session(database=database) as session:
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


def _snapshot_payload(
    nodes: list[NodeInfo],
    stats: dict[str, Any],
    task_profiles: list[dict[str, Any]],
    plans: list[LoadoutPlan],
) -> dict[str, Any]:
    node_rows = [
        {
            "name": node.name,
            "ip": node.ip,
            "online": node.online,
            "loaded_models": sorted(set(node.loaded_models)),
            "all_models": sorted(set(node.all_models)),
            "configured_models": sorted(set(node.configured_models)),
            "model_aliases": dict(sorted(node.model_aliases.items())),
            "latency_ms": node.latency_ms,
            "error": node.error,
            "warnings": list(node.warnings),
            "endpoint_status": dict(sorted(node.endpoint_status.items())),
            "power_watts": node.power_watts,
            "base_url": node.base_url,
            "provider_name": node.provider_name,
            "discovery_source": node.discovery_source,
            "inventory_complete": node.inventory_complete,
            "inventory_authoritative": node.inventory_authoritative,
            "loaded_state_source": node.loaded_state_source,
            "observed_at": node.observed_at,
            "routable_loaded_models": (
                sorted(set(node.loaded_models))
                if node.online and node.inventory_authoritative
                else []
            ),
        }
        for node in nodes
    ]
    observed_models = {
        (str(row["name"]), str(model_id))
        for row in node_rows
        for model_id in row.get("all_models") or []
    }
    loaded_models = {
        (str(row["name"]), str(model_id))
        for row in node_rows
        for model_id in row.get("loaded_models") or []
    }
    routable_models = {
        (str(row["name"]), str(model_id))
        for row in node_rows
        for model_id in row.get("routable_loaded_models") or []
    }
    return {
        "captured_at": utc_now().isoformat(),
        "summary": {
            "observed_node_count": len(node_rows),
            "online_node_count": sum(1 for row in node_rows if row.get("online")),
            "authoritative_node_count": sum(
                1 for row in node_rows if row.get("online") and row.get("inventory_authoritative")
            ),
            "observed_model_count": len(observed_models),
            "loaded_model_count": len(loaded_models),
            "routable_model_count": len(routable_models),
            "task_profile_count": len(task_profiles),
            "loadout_count": len(plans),
        },
        "nodes": node_rows,
        "stats": stats,
        "task_profiles": task_profiles,
        "loadouts": [asdict(plan) for plan in plans],
    }


def _current_snapshot_info(
    driver,
    *,
    database: str = DEFAULT_NEO4J_DB,
) -> dict[str, Any] | None:
    with driver.session(database=database) as session:
        row = session.run(
            """
            MATCH (s:FleetSnapshot)
            WHERE s.status = 'ready' OR s.status IS NULL
            RETURN s.snapshot_id AS snapshot_id, s.captured_at_ms AS captured_at_ms
            ORDER BY coalesce(s.current, false) DESC, coalesce(s.reconciliation_lock_version, 0) DESC, s.captured_at_ms DESC
            LIMIT 1
            """
        ).single()
        if not row:
            return None
        return dict(row)


def _authoritative_model_rows(
    node_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    model_rows: list[dict[str, Any]] = []
    for row in node_rows:
        if (
            not isinstance(row, dict)
            or not row.get("name")
            or not row.get("online")
            or not row.get("inventory_authoritative")
        ):
            continue
        for model_id in sorted(set(row.get("loaded_models") or [])):
            model_rows.append(
                {
                    "model_id": model_id,
                    "node_name": row["name"],
                    "online": True,
                    "latency_ms": row.get("latency_ms"),
                    "loaded": True,
                    "discovery_source": row.get("discovery_source"),
                    "loaded_state_source": row.get("loaded_state_source"),
                    "observed_at": row.get("observed_at"),
                    "inventory_authoritative": True,
                }
            )
    return model_rows


def _validate_snapshot_for_persistence(
    node_rows: list[dict[str, Any]],
    model_rows: list[dict[str, Any]],
    plans: list[LoadoutPlan],
    *,
    allow_empty_snapshot: bool,
) -> None:
    if allow_empty_snapshot:
        return
    if not node_rows:
        raise UnsafeFleetSnapshotError(
            "refusing to replace fleet state with a snapshot containing no nodes"
        )
    if not model_rows:
        raise UnsafeFleetSnapshotError(
            "refusing to replace fleet state without an authoritative loaded model"
        )
    if not plans:
        raise UnsafeFleetSnapshotError(
            "refusing to replace fleet state without any task-profile loadouts"
        )
    missing_primary = sorted(plan.task_profile_id for plan in plans if plan.primary is None)
    if missing_primary:
        raise UnsafeFleetSnapshotError(
            "refusing to publish loadouts without a primary assignment: "
            + ", ".join(missing_primary)
        )


def _ensure_reconciliation_schema(
    driver,
    *,
    database: str,
) -> None:
    """Fail closed unless every reconciliation identity is schema-unique."""

    try:
        for query in RECONCILIATION_SCHEMA_QUERIES:
            driver.execute_query(query, database_=database)
    except Exception as exc:
        raise UnsafeFleetSnapshotError(
            "cannot enforce Neo4j reconciliation uniqueness constraints"
        ) from exc


def _locked_current_snapshot_info(tx, snapshot_id: str) -> dict[str, Any]:
    """Serialize reconcilers and read the predecessor while holding the lock."""

    row = tx.run(
        """
        MERGE (lock:FleetReconciliationLock {name: 'fleet-loadout'})
        SET lock.owner_snapshot_id = $snapshot_id,
            lock.version = coalesce(lock.version, 0) + 1,
            lock.updated_at = datetime()
        WITH lock
        OPTIONAL MATCH (s:FleetSnapshot)
        WHERE s.status = 'ready' OR s.status IS NULL
        WITH lock, s
        ORDER BY coalesce(s.current, false) DESC, coalesce(s.reconciliation_lock_version, 0) DESC, s.captured_at_ms DESC
        RETURN s.snapshot_id AS snapshot_id,
               s.captured_at_ms AS captured_at_ms,
               s.model_count AS model_count,
               s.authoritative_node_count AS authoritative_node_count,
               lock.version AS lock_version
        LIMIT 1
        """,
        snapshot_id=snapshot_id,
    ).single()
    return dict(row) if row else {}


def _validate_snapshot_degradation(
    previous: dict[str, Any],
    *,
    current_model_count: int,
    current_authoritative_node_count: int,
    max_drop_fraction: float,
    allow_degraded_snapshot: bool,
) -> None:
    if allow_degraded_snapshot or not previous.get("snapshot_id"):
        return
    if not 0.0 <= max_drop_fraction <= 1.0:
        raise ValueError("max_drop_fraction must be between 0 and 1")

    regressions: list[str] = []
    for label, previous_key, current_count in (
        ("routable models", "model_count", current_model_count),
        (
            "authoritative nodes",
            "authoritative_node_count",
            current_authoritative_node_count,
        ),
    ):
        previous_count = int(previous.get(previous_key) or 0)
        if previous_count <= 0 or current_count >= previous_count:
            continue
        drop_fraction = (previous_count - current_count) / previous_count
        if drop_fraction > max_drop_fraction:
            regressions.append(
                f"{label} dropped from {previous_count} to {current_count} ({drop_fraction:.1%})"
            )

    if regressions:
        raise UnsafeFleetSnapshotError(
            "refusing degraded fleet snapshot: "
            + "; ".join(regressions)
            + ". Use --allow-degraded-snapshot only for an intentional change."
        )


def persist_to_neo4j(
    driver,
    payload: dict[str, Any],
    plans: list[LoadoutPlan],
    task_profiles: list[dict[str, Any]],
    *,
    database: str = DEFAULT_NEO4J_DB,
    allow_empty_snapshot: bool = False,
    allow_degraded_snapshot: bool = False,
    max_fleet_drop_fraction: float = DEFAULT_MAX_FLEET_DROP_FRACTION,
) -> dict[str, Any]:
    snapshot_id = str(uuid.uuid4())
    captured_at = utc_now()
    captured_at_ms = int(captured_at.timestamp() * 1000)

    node_rows = [
        row for row in payload.get("nodes", []) if isinstance(row, dict) and row.get("name")
    ]
    model_rows = _authoritative_model_rows(node_rows)
    _validate_snapshot_for_persistence(
        node_rows,
        model_rows,
        plans,
        allow_empty_snapshot=allow_empty_snapshot,
    )
    _ensure_reconciliation_schema(driver, database=database)

    node_state_rows: list[dict[str, Any]] = []
    for row in node_rows:
        node_name = str(row["name"])
        node_state_rows.append(
            {
                "node_name": node_name,
                "observation_id": f"{snapshot_id}:{node_name}",
                "snapshot_id": snapshot_id,
                "captured_at_iso": captured_at.isoformat(),
                "online": bool(row.get("online")),
                "ip": row.get("ip"),
                "loaded_model_count": len(row.get("loaded_models") or []),
                "loaded_models": row.get("loaded_models") or [],
                "all_models": row.get("all_models") or [],
                "configured_models": row.get("configured_models") or [],
                "routable_loaded_models": row.get("routable_loaded_models") or [],
                "latency_ms": row.get("latency_ms"),
                "error": row.get("error"),
                "warnings": row.get("warnings") or [],
                "endpoint_status_json": json.dumps(
                    row.get("endpoint_status") or {}, sort_keys=True
                ),
                "power_watts": row.get("power_watts"),
                "base_url": row.get("base_url"),
                "provider_name": row.get("provider_name"),
                "discovery_source": row.get("discovery_source"),
                "inventory_complete": bool(row.get("inventory_complete")),
                "inventory_authoritative": bool(row.get("inventory_authoritative")),
                "loaded_state_source": row.get("loaded_state_source"),
                "observed_at": row.get("observed_at"),
                "raw_json": json.dumps(row, sort_keys=True, default=str),
            }
        )

    model_state_rows: list[dict[str, Any]] = []
    for row in model_rows:
        state_id = f"{row['node_name']}:{row['model_id']}"
        model_state_rows.append(
            {
                **row,
                "state_id": state_id,
                "observation_id": f"{snapshot_id}:{state_id}",
                "node_observation_id": f"{snapshot_id}:{row['node_name']}",
                "snapshot_id": snapshot_id,
                "captured_at_iso": captured_at.isoformat(),
                "raw_json": json.dumps(row, sort_keys=True, default=str),
            }
        )

    loadout_rows: list[dict[str, Any]] = []
    assignment_rows: list[dict[str, Any]] = []
    for plan in plans:
        loadout_id = f"{snapshot_id}:{plan.task_profile_id}"
        loadout_rows.append(
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
            }
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
            state_id = f"{candidate.node_name}:{candidate.model_id}"
            assignment_rows.append(
                {
                    "assignment_id": f"{loadout_id}:{slot_name}",
                    "loadout_id": loadout_id,
                    "snapshot_id": snapshot_id,
                    "task_profile_id": plan.task_profile_id,
                    "slot_name": slot_name,
                    "rank": rank,
                    "node_name": candidate.node_name,
                    "model_id": candidate.model_id,
                    "model_state_id": state_id,
                    "node_observation_id": f"{snapshot_id}:{candidate.node_name}",
                    "model_observation_id": f"{snapshot_id}:{state_id}",
                    "role": candidate.role,
                    "score": candidate.score,
                    "reasons": candidate.reasons,
                }
            )

    summary = payload.get("summary") or {}

    def write_snapshot(tx) -> dict[str, Any]:
        previous = _locked_current_snapshot_info(tx, snapshot_id)
        previous_snapshot_id = str(previous["snapshot_id"]) if previous.get("snapshot_id") else None
        current_authoritative_node_count = int(summary.get("authoritative_node_count") or 0)
        _validate_snapshot_degradation(
            previous,
            current_model_count=len(model_state_rows),
            current_authoritative_node_count=current_authoritative_node_count,
            max_drop_fraction=max_fleet_drop_fraction,
            allow_degraded_snapshot=(allow_degraded_snapshot or allow_empty_snapshot),
        )
        delta_summary = {
            "snapshot_id": snapshot_id,
            "previous_snapshot_id": previous_snapshot_id,
            "reconciliation_lock_version": previous.get("lock_version"),
            "loadout_count": len(plans),
            "changed_loadouts": (
                [plan.task_profile_id for plan in plans] if previous_snapshot_id else []
            ),
        }

        tx.run(
            """
            MERGE (s:FleetSnapshot {snapshot_id: $snapshot_id})
            SET s.captured_at = datetime($captured_at_iso),
                s.captured_at_ms = $captured_at_ms,
                s.status = 'building',
                s.current = false,
                s.source = $source,
                s.node_count = $node_count,
                s.online_node_count = $online_node_count,
                s.authoritative_node_count = $authoritative_node_count,
                s.observed_model_count = $observed_model_count,
                s.loaded_model_count = $loaded_model_count,
                s.model_count = $routable_model_count,
                s.task_profile_count = $task_profile_count,
                s.loadout_count = $loadout_count,
                s.raw_json = $raw_json,
                s.summary_json = $summary_json,
                s.previous_snapshot_id = $previous_snapshot_id,
                s.reconciliation_lock_version = $reconciliation_lock_version,
                s.updated_at = datetime()
            """,
            snapshot_id=snapshot_id,
            captured_at_iso=captured_at.isoformat(),
            captured_at_ms=captured_at_ms,
            source="build_fleet_loadouts.py",
            node_count=len(node_state_rows),
            online_node_count=int(summary.get("online_node_count") or 0),
            authoritative_node_count=int(summary.get("authoritative_node_count") or 0),
            observed_model_count=int(summary.get("observed_model_count") or 0),
            loaded_model_count=int(summary.get("loaded_model_count") or 0),
            routable_model_count=len(model_state_rows),
            task_profile_count=len(task_profiles),
            loadout_count=len(plans),
            raw_json=json.dumps(payload, sort_keys=True, default=str),
            summary_json=json.dumps(summary, sort_keys=True, default=str),
            previous_snapshot_id=previous_snapshot_id,
            reconciliation_lock_version=previous.get("lock_version"),
        )

        # Mutable state nodes represent only the current topology. Snapshot
        # history is preserved separately through immutable observation nodes.
        tx.run("MATCH (:FleetSnapshot)-[r:HAS_NODE_STATE]->() DELETE r")
        tx.run("MATCH (:FleetSnapshot)-[r:HAS_MODEL_STATE]->() DELETE r")

        tx.run(
            """
            UNWIND $rows AS row
            MERGE (n:FleetNodeState {node_name: row.node_name})
            SET n.snapshot_id = row.snapshot_id,
                n.captured_at = datetime(row.captured_at_iso),
                n.online = row.online,
                n.ip = row.ip,
                n.loaded_model_count = row.loaded_model_count,
                n.loaded_models = row.loaded_models,
                n.all_models = row.all_models,
                n.configured_models = row.configured_models,
                n.routable_loaded_models = row.routable_loaded_models,
                n.latency_ms = row.latency_ms,
                n.error = row.error,
                n.warnings = row.warnings,
                n.endpoint_status_json = row.endpoint_status_json,
                n.power_watts = row.power_watts,
                n.base_url = row.base_url,
                n.provider_name = row.provider_name,
                n.discovery_source = row.discovery_source,
                n.inventory_complete = row.inventory_complete,
                n.inventory_authoritative = row.inventory_authoritative,
                n.loaded_state_source = row.loaded_state_source,
                n.observed_at = row.observed_at,
                n.raw_json = row.raw_json,
                n.updated_at = datetime()
            MERGE (o:FleetNodeObservation {observation_id: row.observation_id})
            ON CREATE SET o.created_at = datetime()
            SET o.snapshot_id = row.snapshot_id,
                o.node_name = row.node_name,
                o.captured_at = datetime(row.captured_at_iso),
                o.online = row.online,
                o.ip = row.ip,
                o.loaded_model_count = row.loaded_model_count,
                o.loaded_models = row.loaded_models,
                o.all_models = row.all_models,
                o.configured_models = row.configured_models,
                o.routable_loaded_models = row.routable_loaded_models,
                o.latency_ms = row.latency_ms,
                o.error = row.error,
                o.warnings = row.warnings,
                o.endpoint_status_json = row.endpoint_status_json,
                o.power_watts = row.power_watts,
                o.base_url = row.base_url,
                o.provider_name = row.provider_name,
                o.discovery_source = row.discovery_source,
                o.inventory_complete = row.inventory_complete,
                o.inventory_authoritative = row.inventory_authoritative,
                o.loaded_state_source = row.loaded_state_source,
                o.observed_at = row.observed_at,
                o.raw_json = row.raw_json
            WITH n, o, row
            MATCH (s:FleetSnapshot {snapshot_id: row.snapshot_id})
            MERGE (s)-[:HAS_NODE_STATE]->(n)
            MERGE (s)-[:HAS_NODE_OBSERVATION]->(o)
            """,
            rows=node_state_rows,
        )

        tx.run(
            """
            UNWIND $rows AS row
            MERGE (m:FleetModelState {state_id: row.state_id})
            SET m.model_id = row.model_id,
                m.snapshot_id = row.snapshot_id,
                m.captured_at = datetime(row.captured_at_iso),
                m.node_name = row.node_name,
                m.online = row.online,
                m.loaded = row.loaded,
                m.latency_ms = row.latency_ms,
                m.discovery_source = row.discovery_source,
                m.loaded_state_source = row.loaded_state_source,
                m.observed_at = row.observed_at,
                m.inventory_authoritative = row.inventory_authoritative,
                m.raw_json = row.raw_json,
                m.updated_at = datetime()
            MERGE (o:FleetModelObservation {observation_id: row.observation_id})
            ON CREATE SET o.created_at = datetime()
            SET o.snapshot_id = row.snapshot_id,
                o.state_id = row.state_id,
                o.model_id = row.model_id,
                o.node_name = row.node_name,
                o.captured_at = datetime(row.captured_at_iso),
                o.online = row.online,
                o.loaded = row.loaded,
                o.latency_ms = row.latency_ms,
                o.discovery_source = row.discovery_source,
                o.loaded_state_source = row.loaded_state_source,
                o.observed_at = row.observed_at,
                o.inventory_authoritative = row.inventory_authoritative,
                o.raw_json = row.raw_json
            WITH m, o, row
            MATCH (s:FleetSnapshot {snapshot_id: row.snapshot_id})
            MATCH (n:FleetNodeState {node_name: row.node_name})
            MATCH (node_observation:FleetNodeObservation {
                observation_id: row.node_observation_id
            })
            MERGE (s)-[:HAS_MODEL_STATE]->(m)
            MERGE (s)-[:HAS_MODEL_OBSERVATION]->(o)
            MERGE (n)-[:HOSTS_MODEL]->(m)
            MERGE (node_observation)-[:HOSTED_MODEL_OBSERVATION]->(o)
            """,
            rows=model_state_rows,
        )

        tx.run(
            """
            UNWIND $rows AS row
            MERGE (l:FleetLoadout {loadout_id: row.loadout_id})
            SET l.snapshot_id = row.snapshot_id,
                l.task_profile_id = row.task_profile_id,
                l.task_profile_name = row.task_profile_name,
                l.score = row.score,
                l.rationale = row.rationale,
                l.primary_node = row.primary_node,
                l.primary_model = row.primary_model,
                l.reviewer_node = row.reviewer_node,
                l.reviewer_model = row.reviewer_model,
                l.fallback_node = row.fallback_node,
                l.fallback_model = row.fallback_model,
                l.updated_at = datetime()
            WITH l, row
            MATCH (s:FleetSnapshot {snapshot_id: row.snapshot_id})
            MATCH (p:FleetTaskProfile {id: row.task_profile_id})
            MERGE (s)-[:HAS_LOADOUT]->(l)
            MERGE (l)-[:FOR_PROFILE]->(p)
            """,
            rows=loadout_rows,
        )

        tx.run(
            """
            UNWIND $rows AS row
            MERGE (a:FleetLoadoutAssignment {assignment_id: row.assignment_id})
            SET a.loadout_id = row.loadout_id,
                a.snapshot_id = row.snapshot_id,
                a.task_profile_id = row.task_profile_id,
                a.slot_name = row.slot_name,
                a.rank = row.rank,
                a.node_name = row.node_name,
                a.model_id = row.model_id,
                a.role = row.role,
                a.score = row.score,
                a.reasons = row.reasons,
                a.updated_at = datetime()
            WITH a, row
            MATCH (l:FleetLoadout {loadout_id: row.loadout_id})
            MATCH (n:FleetNodeState {node_name: row.node_name})
            MATCH (m:FleetModelState {state_id: row.model_state_id})
            MATCH (node_observation:FleetNodeObservation {
                observation_id: row.node_observation_id
            })
            MATCH (model_observation:FleetModelObservation {
                observation_id: row.model_observation_id
            })
            MERGE (l)-[:HAS_ASSIGNMENT]->(a)
            MERGE (a)-[:USES_NODE]->(n)
            MERGE (a)-[:USES_MODEL]->(m)
            MERGE (a)-[:USES_NODE_OBSERVATION]->(node_observation)
            MERGE (a)-[:USES_MODEL_OBSERVATION]->(model_observation)
            """,
            rows=assignment_rows,
        )

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
            delta_json=json.dumps(delta_summary, sort_keys=True),
        )

        tx.run(
            """
            MATCH (n:FleetNodeState)
            WHERE NOT n.node_name IN $current_node_names
            DETACH DELETE n
            """,
            current_node_names=[row["node_name"] for row in node_state_rows],
        )
        tx.run(
            """
            MATCH (m:FleetModelState)
            WHERE NOT m.state_id IN $current_model_ids
            DETACH DELETE m
            """,
            current_model_ids=[row["state_id"] for row in model_state_rows],
        )
        tx.run(
            """
            MATCH (old:FleetSnapshot)
            WHERE old.current = true AND old.snapshot_id <> $snapshot_id
            SET old.current = false
            """,
            snapshot_id=snapshot_id,
        )
        tx.run(
            """
            MATCH (s:FleetSnapshot {snapshot_id: $snapshot_id})
            SET s.status = 'ready',
                s.current = true,
                s.completed_at = datetime(),
                s.updated_at = datetime()
            """,
            snapshot_id=snapshot_id,
        )
        return delta_summary

    with driver.session(database=database) as session:
        committed_delta = session.execute_write(write_snapshot)

    return {
        "snapshot_id": snapshot_id,
        "captured_at": captured_at.isoformat(),
        "previous_snapshot_id": committed_delta.get("previous_snapshot_id"),
        "reconciliation_lock_version": committed_delta.get("reconciliation_lock_version"),
        "node_count": len(node_state_rows),
        "model_count": len(model_state_rows),
        "loadout_count": len(plans),
        "task_profile_count": len(task_profiles),
        "delta": committed_delta,
    }


def publish_committed_report(
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


def build_report(
    nodes: list[NodeInfo], stats: dict[str, Any], task_profiles: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[LoadoutPlan]]:
    plans = [plan_loadout(profile, nodes, stats) for profile in task_profiles]
    plans = sorted(plans, key=lambda plan: plan.score, reverse=True)
    return _snapshot_payload(nodes, stats, task_profiles, plans), plans


def print_report(report: dict[str, Any]) -> None:
    print(f"Snapshot: {report['captured_at']}")
    print(
        f"Nodes: {len(report.get('nodes', []))} | Task profiles: {len(report.get('task_profiles', []))} | Loadouts: {len(report.get('loadouts', []))}"
    )
    print("")
    for loadout in report.get("loadouts", []):
        primary = loadout.get("primary") or {}
        reviewer = loadout.get("reviewer") or {}
        fallback = loadout.get("fallback") or {}
        print(
            f"- {loadout.get('task_profile_id')} -> score {loadout.get('score')} | {loadout.get('rationale')}"
        )
        if primary:
            print(
                f"  primary:  {primary.get('node_name')}/{primary.get('model_id')} ({primary.get('role')}, score={primary.get('score')})"
            )
        if reviewer:
            print(
                f"  reviewer: {reviewer.get('node_name')}/{reviewer.get('model_id')} ({reviewer.get('role')}, score={reviewer.get('score')})"
            )
        if fallback:
            print(
                f"  fallback: {fallback.get('node_name')}/{fallback.get('model_id')} ({fallback.get('role')}, score={fallback.get('score')})"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build fleet loadouts and persist them to Neo4j")
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI)
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER)
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD)
    parser.add_argument("--neo4j-database", default=DEFAULT_NEO4J_DB)
    parser.add_argument("--stats-path", type=Path, default=DEFAULT_STATS_PATH)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write to Neo4j; just print and save the JSON report",
    )
    parser.add_argument(
        "--allow-empty-snapshot",
        action="store_true",
        help=("Explicitly allow an empty/non-routable snapshot to replace current fleet state"),
    )
    parser.add_argument(
        "--allow-degraded-snapshot",
        action="store_true",
        help=("Explicitly allow a fleet topology drop larger than the configured safety threshold"),
    )
    parser.add_argument(
        "--max-fleet-drop-fraction",
        type=float,
        default=DEFAULT_MAX_FLEET_DROP_FRACTION,
        help="Maximum tolerated routable-model or authoritative-node drop (0-1)",
    )
    args = parser.parse_args()
    if not 0.0 <= args.max_fleet_drop_fraction <= 1.0:
        parser.error("--max-fleet-drop-fraction must be between 0 and 1")

    stats = load_json(args.stats_path)
    nodes = probe_all_nodes()

    driver = None
    try:
        if not args.dry_run:
            if not args.neo4j_password:
                parser.error(
                    "NEO4J_PASSWORD or --neo4j-password is required unless --dry-run is used"
                )
            driver = GraphDatabase.driver(
                args.neo4j_uri,
                auth=(args.neo4j_user, args.neo4j_password),
            )
            task_profiles = read_task_profiles(
                driver,
                database=args.neo4j_database,
            )
        else:
            task_profiles = [
                {"id": value, "name": value.replace("_", " ").title()}
                for value in PROFILE_ALIASES.values()
            ]

        report, plans = build_report(nodes, stats, task_profiles)
        print_report(report)

        if args.dry_run:
            report["publication"] = {
                "mode": "dry-run",
                "graph_persisted": False,
            }
            atomic_write_json(args.report_path, report)
            return 0

        persist_result = persist_to_neo4j(
            driver,
            report,
            plans,
            task_profiles,
            database=args.neo4j_database,
            allow_empty_snapshot=args.allow_empty_snapshot,
            allow_degraded_snapshot=args.allow_degraded_snapshot,
            max_fleet_drop_fraction=args.max_fleet_drop_fraction,
        )
        report["persistence"] = persist_result
        report["publication"] = {
            "mode": "committed",
            "graph_persisted": True,
            "snapshot_id": persist_result["snapshot_id"],
            "reconciliation_lock_version": persist_result["reconciliation_lock_version"],
        }
        report_published = publish_committed_report(
            driver,
            args.report_path,
            report,
            persist_result,
            database=args.neo4j_database,
        )
        print("")
        print(
            f"Persisted snapshot {persist_result['snapshot_id']} with {persist_result['loadout_count']} loadouts"
        )
        if persist_result.get("previous_snapshot_id"):
            print(f"Previous snapshot: {persist_result['previous_snapshot_id']}")
        if not report_published:
            print(
                "Skipped JSON report publication because a newer fleet snapshot "
                "already owns the reconciliation fence"
            )
        return 0
    finally:
        if driver is not None:
            driver.close()


if __name__ == "__main__":
    raise SystemExit(main())
