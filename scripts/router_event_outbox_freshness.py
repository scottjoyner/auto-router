#!/usr/bin/env python3
"""Read-only event-outbox freshness and new-401 witness.

Never reads event payloads or returns raw last_error fields. Passing means
local SQLite freshness only, NOT authenticated delivery or deployment GO.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from urllib.parse import quote

COLUMNS = {"status", "updated_at", "last_error"}
STATES = {"pending", "retry", "delivered", "dead_letter"}
SCHEMA = "router-outbox-freshness-witness/v1"


class WitnessHold(Exception):
    pass


def _safe_db_path(database: Path) -> Path:
    if not database.is_absolute() or ".." in database.parts:
        raise WitnessHold("unqualified_database_path")
    for component in (database, *database.parents):
        try:
            info = component.lstat()
        except OSError:
            raise WitnessHold("database_not_accessible") from None
        if stat.S_ISLNK(info.st_mode):
            raise WitnessHold("database_symlink_component")
    if not stat.S_ISREG(database.stat().st_mode):
        raise WitnessHold("database_not_regular")
    return database


def _iso(unix_timestamp: int | None) -> str | None:
    if unix_timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(unix_timestamp, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        raise WitnessHold("invalid_timestamp") from None


def inspect(
    database: Path, *, cutoff: int, now: int, freshness_seconds: int = 86400
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema": SCHEMA,
        "verdict": "HOLD",
        "deployment_authorized": False,
        "authenticated_delivery_proven": False,
        "outbox_replayed": False,
        "events_mutated": 0,
        "event_payloads_read": 0,
        "raw_errors_read": 0,
    }
    try:
        if not (0 <= cutoff <= now) or freshness_seconds <= 0:
            raise WitnessHold("invalid_time_window")
        path = _safe_db_path(database)
        uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=1)
            try:
                con.execute("PRAGMA query_only=ON")
                # No INSERT/DELETE/DDL and no SELECT of payload, event identity,
                # source-service, idempotency or raw last_error text.
                tables = {
                    row[0] for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "event_outbox" not in tables:
                    raise WitnessHold("outbox_table_missing")
                columns = {
                    row[1] for row in con.execute(
                        "PRAGMA table_info(event_outbox)"
                    )
                }
                if not COLUMNS.issubset(columns):
                    raise WitnessHold("outbox_columns_missing")
                groups = {
                    str(state): int(count)
                    for state, count in con.execute(
                        "SELECT status,COUNT(*) FROM event_outbox GROUP BY status"
                    )
                }
                if set(groups) - STATES:
                    raise WitnessHold("unexpected_outbox_status")
                last_delivery = con.execute(
                    "SELECT MAX(updated_at) FROM event_outbox "
                    "WHERE status='delivered'"
                ).fetchone()[0]
                latest_row = con.execute(
                    "SELECT MAX(updated_at) FROM event_outbox"
                ).fetchone()[0]
                new_dead_letters = con.execute(
                    "SELECT COUNT(*) FROM event_outbox "
                    "WHERE status='dead_letter' AND updated_at>=?",
                    (cutoff,),
                ).fetchone()[0]
                new_http_401 = con.execute(
                    "SELECT COUNT(*) FROM event_outbox "
                    "WHERE status='dead_letter' AND updated_at>=? "
                    "AND last_error LIKE 'AssistX returned HTTP 401%'",
                    (cutoff,),
                ).fetchone()[0]
            finally:
                con.close()
        except sqlite3.Error:
            raise WitnessHold("sqlite_read_unavailable") from None

        for stamp in (last_delivery, latest_row):
            if stamp is not None and (
                type(stamp) is not int or stamp < 0 or stamp > now + 60
            ):
                raise WitnessHold("invalid_or_future_record_timestamp")
        result.update({
            "counts": {state: groups.get(state, 0) for state in sorted(STATES)},
            "new_dead_letters_since_cutoff": int(new_dead_letters),
            "new_http_401_dead_letters_since_cutoff": int(new_http_401),
            "cutoff_utc": _iso(cutoff),
            "last_delivery_utc": _iso(last_delivery),
            "latest_activity_utc": _iso(latest_row),
            "recent_delivery_observed": (
                last_delivery is not None
                and last_delivery >= now - freshness_seconds
            ),
        })
        if new_http_401 > 0:
            raise WitnessHold("new_auth_denied_dead_letters")
        if new_dead_letters > 0:
            raise WitnessHold("new_dead_letters")
        if groups.get("pending", 0) or groups.get("retry", 0):
            raise WitnessHold("pending_or_retry_not_drained")
        if not result["recent_delivery_observed"]:
            raise WitnessHold("no_recent_delivery_proof")
        result["verdict"] = "FRESHNESS_OBSERVED_ONLY"
        result["reason"] = "not_a_validated_authenticated_sink_or_release_gate"
        return result
    except WitnessHold as err:
        result["reason"] = str(err)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Non-promoting readonly SQLite outbox freshness witness")
    parser.add_argument("--database-path", type=Path, required=True)
    parser.add_argument("--since-unix", type=int, required=True)
    parser.add_argument("--now-unix", type=int, default=None)
    parser.add_argument("--freshness-seconds", type=int, default=86400)
    args = parser.parse_args()
    verdict = inspect(
        args.database_path, cutoff=args.since_unix,
        now=int(time.time()) if args.now_unix is None else args.now_unix,
        freshness_seconds=args.freshness_seconds,
    )
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict["verdict"] == "FRESHNESS_OBSERVED_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
