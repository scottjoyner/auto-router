#!/usr/bin/env python3
"""Apply the final reviewed reconciliation safety patch.

The companion workflow validates the transformed source and removes this file
and itself in the generated commit.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_fleet_loadouts.py"
README = ROOT / "README.md"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {label} match, found {count}")
    return text.replace(old, new, 1)


def patch_builder() -> None:
    text = BUILDER.read_text(encoding="utf-8")

    text = replace_once(
        text,
        'DEFAULT_NEO4J_DB = os.getenv("NEO4J_DATABASE", os.getenv("NEO4J_DB", "neo4j"))\n',
        'DEFAULT_NEO4J_DB = os.getenv("NEO4J_DATABASE", os.getenv("NEO4J_DB", "neo4j"))\nDEFAULT_MAX_FLEET_DROP_FRACTION = float(\n    os.getenv("AUTO_ROUTER_MAX_FLEET_DROP_FRACTION", "0.5")\n)\n',
        label="degradation threshold constant",
    )

    helpers = '''def _locked_current_snapshot_info(tx, snapshot_id: str) -> dict[str, Any]:
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
        ORDER BY coalesce(s.current, false) DESC, s.captured_at_ms DESC
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
                f"{label} dropped from {previous_count} to {current_count} "
                f"({drop_fraction:.1%})"
            )

    if regressions:
        raise UnsafeFleetSnapshotError(
            "refusing degraded fleet snapshot: "
            + "; ".join(regressions)
            + ". Use --allow-degraded-snapshot only for an intentional change."
        )
'''
    text = replace_once(
        text,
        "def persist_to_neo4j(\n",
        helpers + "\n\n\ndef persist_to_neo4j(\n",
        label="serialized degradation helpers",
    )

    text = replace_once(
        text,
        '''    database: str = DEFAULT_NEO4J_DB,
    allow_empty_snapshot: bool = False,
) -> dict[str, Any]:
    snapshot_id = str(uuid.uuid4())
    captured_at = utc_now()
    captured_at_ms = int(captured_at.timestamp() * 1000)
    previous = _current_snapshot_info(driver, database=database)
    previous_snapshot_id = str(previous["snapshot_id"]) if previous else None
''',
        '''    database: str = DEFAULT_NEO4J_DB,
    allow_empty_snapshot: bool = False,
    allow_degraded_snapshot: bool = False,
    max_fleet_drop_fraction: float = DEFAULT_MAX_FLEET_DROP_FRACTION,
) -> dict[str, Any]:
    snapshot_id = str(uuid.uuid4())
    captured_at = utc_now()
    captured_at_ms = int(captured_at.timestamp() * 1000)
''',
        label="persistence arguments",
    )

    delta_start = text.index("    delta_summary = {", text.index("def persist_to_neo4j("))
    summary_marker = "    summary = payload.get(\"summary\") or {}\n"
    summary_index = text.index(summary_marker, delta_start)
    text = text[:delta_start] + summary_marker + text[summary_index + len(summary_marker) :]

    text = replace_once(
        text,
        '''    def write_snapshot(tx) -> dict[str, Any]:
        tx.run(
''',
        '''    def write_snapshot(tx) -> dict[str, Any]:
        previous = _locked_current_snapshot_info(tx, snapshot_id)
        previous_snapshot_id = (
            str(previous["snapshot_id"]) if previous.get("snapshot_id") else None
        )
        current_authoritative_node_count = int(
            summary.get("authoritative_node_count") or 0
        )
        _validate_snapshot_degradation(
            previous,
            current_model_count=len(model_state_rows),
            current_authoritative_node_count=current_authoritative_node_count,
            max_drop_fraction=max_fleet_drop_fraction,
            allow_degraded_snapshot=(
                allow_degraded_snapshot or allow_empty_snapshot
            ),
        )
        delta_summary = {
            "snapshot_id": snapshot_id,
            "previous_snapshot_id": previous_snapshot_id,
            "reconciliation_lock_version": previous.get("lock_version"),
            "loadout_count": len(plans),
            "changed_loadouts": (
                [plan.task_profile_id for plan in plans]
                if previous_snapshot_id
                else []
            ),
        }

        tx.run(
''',
        label="transaction lock and degradation validation",
    )

    text = replace_once(
        text,
        '''                s.previous_snapshot_id = $previous_snapshot_id,
                s.updated_at = datetime()
''',
        '''                s.previous_snapshot_id = $previous_snapshot_id,
                s.reconciliation_lock_version = $reconciliation_lock_version,
                s.updated_at = datetime()
''',
        label="snapshot lock version property",
    )
    text = replace_once(
        text,
        '''            previous_snapshot_id=previous_snapshot_id,
        )

        # Mutable state nodes represent only the current topology.
''',
        '''            previous_snapshot_id=previous_snapshot_id,
            reconciliation_lock_version=previous.get("lock_version"),
        )

        # Mutable state nodes represent only the current topology.
''',
        label="snapshot lock version parameter",
    )
    text = replace_once(
        text,
        '''        "previous_snapshot_id": previous_snapshot_id,
        "node_count": len(node_state_rows),
''',
        '''        "previous_snapshot_id": committed_delta.get("previous_snapshot_id"),
        "reconciliation_lock_version": committed_delta.get(
            "reconciliation_lock_version"
        ),
        "node_count": len(node_state_rows),
''',
        label="committed predecessor result",
    )

    text = replace_once(
        text,
        '''    parser.add_argument(
        "--allow-empty-snapshot",
        action="store_true",
        help=("Explicitly allow an empty/non-routable snapshot to replace current fleet state"),
    )
    args = parser.parse_args()

    stats = load_json(args.stats_path)
''',
        '''    parser.add_argument(
        "--allow-empty-snapshot",
        action="store_true",
        help=("Explicitly allow an empty/non-routable snapshot to replace current fleet state"),
    )
    parser.add_argument(
        "--allow-degraded-snapshot",
        action="store_true",
        help=(
            "Explicitly allow a fleet topology drop larger than the configured "
            "safety threshold"
        ),
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
''',
        label="degradation CLI controls",
    )

    text = replace_once(
        text,
        '''        report, plans = build_report(nodes, stats, task_profiles)
        atomic_write_json(args.report_path, report)
        print_report(report)

        if args.dry_run:
            return 0

        persist_result = persist_to_neo4j(
            driver,
            report,
            plans,
            task_profiles,
            database=args.neo4j_database,
            allow_empty_snapshot=args.allow_empty_snapshot,
        )
''',
        '''        report, plans = build_report(nodes, stats, task_profiles)
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
        }
        atomic_write_json(args.report_path, report)
''',
        label="post-commit report publication",
    )

    BUILDER.write_text(text, encoding="utf-8")


def patch_readme() -> None:
    text = README.read_text(encoding="utf-8")
    old = '''A normal run fails closed when discovery contains no authoritative loaded model,
no task profiles, or a loadout without a primary assignment. This preserves the
last known-good topology during a discovery outage. `--allow-empty-snapshot` is
an explicit destructive override intended only for a deliberate fleet drain.
Reports are published atomically, and Neo4j writes use one transaction so a
failed reconciliation cannot expose a partially built topology.
'''
    new = '''A normal run fails closed when discovery contains no authoritative loaded model,
no task profiles, or a loadout without a primary assignment. It also serializes
reconcilers and rejects routable-model or authoritative-node drops greater than
50% by default, preserving the last known-good topology during partial discovery
outages. Tune the threshold with `AUTO_ROUTER_MAX_FLEET_DROP_FRACTION` or
`--max-fleet-drop-fraction`. `--allow-degraded-snapshot` and
`--allow-empty-snapshot` are explicit destructive overrides for intentional
fleet changes or drains. Reports are published atomically only after the Neo4j
transaction commits, so rejected or partial reconciliation attempts cannot
replace the last committed report or expose a partially built graph topology.
'''
    text = replace_once(text, old, new, label="README reconciliation guarantees")
    README.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    patch_builder()
    patch_readme()
