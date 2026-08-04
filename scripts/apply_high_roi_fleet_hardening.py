#!/usr/bin/env python3
"""Apply the reviewed fleet reconciliation hardening patch.

This file is intentionally temporary. The companion workflow validates the
result and removes both itself and this patcher in the generated commit.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "build_fleet_loadouts.py"
README = ROOT / "README.md"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {label} match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str) -> str:
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    return text[:start_index] + replacement.rstrip() + "\n\n\n" + text[end_index:]


def patch_builder() -> None:
    text = TARGET.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "import sys\nimport uuid\nfrom dataclasses import dataclass, asdict\n",
        "import sys\nimport tempfile\nimport uuid\nfrom dataclasses import asdict, dataclass\n",
        label="stdlib import block",
    )
    text = replace_once(
        text,
        'DEFAULT_STATS_PATH = Path(os.getenv("AUTO_ROUTER_FLEET_DISPATCHER_STATS_PATH", "/home/scott/git/auto-router/data/fleet_dispatcher_stats.json"))\nDEFAULT_REPORT_PATH = Path(os.getenv("AUTO_ROUTER_FLEET_LOADOUT_REPORT_PATH", "/home/scott/git/auto-router/data/fleet_loadout_report.json"))\n',
        'DEFAULT_STATS_PATH = Path(\n    os.getenv(\n        "AUTO_ROUTER_FLEET_DISPATCHER_STATS_PATH",\n        str(REPO_ROOT / "data" / "fleet_dispatcher_stats.json"),\n    )\n)\nDEFAULT_REPORT_PATH = Path(\n    os.getenv(\n        "AUTO_ROUTER_FLEET_LOADOUT_REPORT_PATH",\n        str(REPO_ROOT / "data" / "fleet_loadout_report.json"),\n    )\n)\n',
        label="portable data paths",
    )
    text = replace_once(
        text,
        "class LoadoutPlan:\n    task_profile_id: str\n    task_profile_name: str\n    primary: Candidate | None\n    reviewer: Candidate | None\n    fallback: Candidate | None\n    score: float\n    rationale: str\n\n\ndef utc_now() -> datetime:\n",
        "class LoadoutPlan:\n    task_profile_id: str\n    task_profile_name: str\n    primary: Candidate | None\n    reviewer: Candidate | None\n    fallback: Candidate | None\n    score: float\n    rationale: str\n\n\nclass UnsafeFleetSnapshotError(RuntimeError):\n    \"\"\"Raised when reconciliation would replace useful state with an unsafe snapshot.\"\"\"\n\n\ndef utc_now() -> datetime:\n",
        label="unsafe snapshot exception",
    )
    text = replace_once(
        text,
        "def _node_stat_map(stats: dict[str, Any]) -> dict[str, dict[str, int]]:\n",
        '''def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
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
            handle.write("\\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _node_stat_map(stats: dict[str, Any]) -> dict[str, dict[str, int]]:
''',
        label="atomic JSON helper",
    )

    selection = '''def pick_best(
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

    rationale_parts = [
        rationale_by_profile.get(profile_id, "default highest-scoring candidate")
    ]
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
'''
    text = replace_between(text, "def pick_best(", "def read_task_profiles(", selection)

    snapshot = '''def _snapshot_payload(
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
                1
                for row in node_rows
                if row.get("online") and row.get("inventory_authoritative")
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
'''
    text = replace_between(text, "def _snapshot_payload(", "def _current_snapshot_info(", snapshot)

    current_snapshot = '''def _current_snapshot_info(
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
            ORDER BY coalesce(s.current, false) DESC, s.captured_at_ms DESC
            LIMIT 1
            """
        ).single()
        if not row:
            return None
        return dict(row)
'''
    text = replace_between(
        text,
        "def _current_snapshot_info(",
        "def _authoritative_model_rows(",
        current_snapshot,
    )

    validation = '''def _validate_snapshot_for_persistence(
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
    missing_primary = sorted(
        plan.task_profile_id for plan in plans if plan.primary is None
    )
    if missing_primary:
        raise UnsafeFleetSnapshotError(
            "refusing to publish loadouts without a primary assignment: "
            + ", ".join(missing_primary)
        )
'''
    text = replace_once(
        text,
        "def persist_to_neo4j(\n",
        validation + "\n\n\ndef persist_to_neo4j(\n",
        label="snapshot validation insertion",
    )

    persistence = '''def persist_to_neo4j(
    driver,
    payload: dict[str, Any],
    plans: list[LoadoutPlan],
    task_profiles: list[dict[str, Any]],
    *,
    database: str = DEFAULT_NEO4J_DB,
    allow_empty_snapshot: bool = False,
) -> dict[str, Any]:
    snapshot_id = str(uuid.uuid4())
    captured_at = utc_now()
    captured_at_ms = int(captured_at.timestamp() * 1000)
    previous = _current_snapshot_info(driver, database=database)
    previous_snapshot_id = str(previous["snapshot_id"]) if previous else None

    node_rows = [
        row
        for row in payload.get("nodes", [])
        if isinstance(row, dict) and row.get("name")
    ]
    model_rows = _authoritative_model_rows(node_rows)
    _validate_snapshot_for_persistence(
        node_rows,
        model_rows,
        plans,
        allow_empty_snapshot=allow_empty_snapshot,
    )

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
                "inventory_authoritative": bool(
                    row.get("inventory_authoritative")
                ),
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

    delta_summary = {
        "snapshot_id": snapshot_id,
        "previous_snapshot_id": previous_snapshot_id,
        "loadout_count": len(plans),
        "changed_loadouts": (
            [plan.task_profile_id for plan in plans]
            if previous_snapshot_id
            else []
        ),
    }
    summary = payload.get("summary") or {}

    def write_snapshot(tx) -> dict[str, Any]:
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
                s.updated_at = datetime()
            """,
            snapshot_id=snapshot_id,
            captured_at_iso=captured_at.isoformat(),
            captured_at_ms=captured_at_ms,
            source="build_fleet_loadouts.py",
            node_count=len(node_state_rows),
            online_node_count=int(summary.get("online_node_count") or 0),
            authoritative_node_count=int(
                summary.get("authoritative_node_count") or 0
            ),
            observed_model_count=int(summary.get("observed_model_count") or 0),
            loaded_model_count=int(summary.get("loaded_model_count") or 0),
            routable_model_count=len(model_state_rows),
            task_profile_count=len(task_profiles),
            loadout_count=len(plans),
            raw_json=json.dumps(payload, sort_keys=True, default=str),
            summary_json=json.dumps(summary, sort_keys=True, default=str),
            previous_snapshot_id=previous_snapshot_id,
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
        "previous_snapshot_id": previous_snapshot_id,
        "node_count": len(node_state_rows),
        "model_count": len(model_state_rows),
        "loadout_count": len(plans),
        "task_profile_count": len(task_profiles),
        "delta": committed_delta,
    }
'''
    text = replace_between(text, "def persist_to_neo4j(", "def build_report(", persistence)

    text = replace_once(
        text,
        '    parser.add_argument("--dry-run", action="store_true", help="Do not write to Neo4j; just print and save the JSON report")\n',
        '    parser.add_argument("--dry-run", action="store_true", help="Do not write to Neo4j; just print and save the JSON report")\n    parser.add_argument(\n        "--allow-empty-snapshot",\n        action="store_true",\n        help=(\n            "Explicitly allow an empty/non-routable snapshot to replace current "\n            "fleet state"\n        ),\n    )\n',
        label="allow empty CLI flag",
    )
    text = replace_once(
        text,
        '        args.report_path.parent.mkdir(parents=True, exist_ok=True)\n        args.report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")\n',
        "        atomic_write_json(args.report_path, report)\n",
        label="atomic report write",
    )
    text = replace_once(
        text,
        "            database=args.neo4j_database,\n        )\n",
        "            database=args.neo4j_database,\n            allow_empty_snapshot=args.allow_empty_snapshot,\n        )\n",
        label="allow empty persistence argument",
    )
    TARGET.write_text(text, encoding="utf-8")


def patch_readme() -> None:
    text = README.read_text(encoding="utf-8")
    marker = "## Routing policy\n"
    section = '''## Fleet loadout reconciliation safety

The fleet loadout builder reads live runtime inventory and writes both a current
routing view and immutable snapshot observations. Production reconciliation
requires explicit Neo4j credentials:

```bash
export NEO4J_URI=bolt://127.0.0.1:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD='set-in-a-secret-store'
export NEO4J_DATABASE=neo4j
python scripts/build_fleet_loadouts.py
```

A normal run fails closed when discovery contains no authoritative loaded model,
no task profiles, or a loadout without a primary assignment. This preserves the
last known-good topology during a discovery outage. `--allow-empty-snapshot` is
an explicit destructive override intended only for a deliberate fleet drain.
Reports are published atomically, and Neo4j writes use one transaction so a
failed reconciliation cannot expose a partially built topology.

'''
    if "## Fleet loadout reconciliation safety" not in text:
        text = replace_once(text, marker, section + marker, label="README safety section")
    README.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    patch_builder()
    patch_readme()
