#!/usr/bin/env python3
"""Read-only SQLite provenance migration/restore rehearsal.

Never invokes UsageLedger or opens the source with write permission. All
ALTERs and rollback operations target anonymous in-memory databases.
Does not copy real row values into output, files, logs, or messages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import stat
import sys
from pathlib import Path
from urllib.parse import quote

TABLES = ("usage_events", "runtime_samples")
DEFAULT_LIMIT = 3 * 1024**3
MIN_AVAILABLE = 8 * 1024**3
COLUMN_DDL = "token_usage_source TEXT NOT NULL DEFAULT 'legacy_unverified'"


def memory_available() -> int:
    with open("/proc/meminfo", encoding="ascii") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    raise ValueError("memory_available_missing")


def source_uri(path: Path) -> str:
    # Encode arbitrary filesystem filenames without allowing SQLite URI
    # query arguments supplied by an untrusted path.
    return "file:" + quote(str(path.absolute()), safe="/") + "?mode=ro"


def columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    if table not in TABLES:
        raise ValueError("invalid_table_name")
    return tuple(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))


def schema_digest(conn: sqlite3.Connection) -> str:
    definitions = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "ORDER BY type, name, tbl_name, sql"
    )
    hasher = hashlib.sha256()
    for record in definitions:
        hasher.update(json.dumps(tuple(record), sort_keys=True).encode() + b"\n")
    return hasher.hexdigest()


def preserved_rows(conn: sqlite3.Connection, table: str) -> dict:
    if table not in TABLES:
        raise ValueError("invalid_table_name")
    digest = hashlib.sha256()
    count = 0
    for row in conn.execute(
        f"SELECT id, input_tokens, output_tokens, total_tokens FROM {table} ORDER BY id"
    ):
        digest.update(json.dumps(tuple(row), separators=(",", ":")).encode() + b"\n")
        count += 1
    return {"rows": count, "counters_sha256": digest.hexdigest(),
            "indexes": len(conn.execute(f"PRAGMA index_list({table})").fetchall())}


def signature(conn: sqlite3.Connection) -> dict:
    return {"schema_sha256": schema_digest(conn),
            "tables": {name: preserved_rows(conn, name) for name in TABLES}}


def apply_copy_migration(target: sqlite3.Connection) -> None:
    for table in TABLES:
        fields = columns(target, table)
        if not fields:
            raise ValueError("missing_required_table")
        if not {"id", "input_tokens", "output_tokens", "total_tokens"}.issubset(fields):
            raise ValueError("missing_required_counter_columns")
        if "token_usage_source" not in fields:
            target.execute(f"ALTER TABLE {table} ADD COLUMN {COLUMN_DDL}")
    target.commit()


def audit(db: Path, *, max_db_bytes: int = DEFAULT_LIMIT) -> dict:
    # Guard before connecting: don't inadvertently copy an arbitrarily
    # large or off-host database into x1's memory.
    metadata = db.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("source_must_be_regular_file_no_symlink")
    if metadata.st_size > max_db_bytes:
        raise ValueError("source_exceeds_bound")
    if memory_available() < max(MIN_AVAILABLE, metadata.st_size * 3):
        raise ValueError("insufficient_memory_for_two_in_memory_copies")

    source = sqlite3.connect(source_uri(db), uri=True, timeout=8)
    source.row_factory = sqlite3.Row
    before = None
    candidate = None
    try:
        source.execute("PRAGMA query_only=ON")
        if source.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise ValueError("source_read_only_failed")
        if source.execute("PRAGMA database_list").fetchone()[2] != str(db.absolute()):
            raise ValueError("wrong_database_opened")
        before = sqlite3.connect(":memory:", timeout=8)
        before.row_factory = sqlite3.Row
        candidate = sqlite3.connect(":memory:", timeout=8)
        candidate.row_factory = sqlite3.Row
        # Two copies are from the same read-only source snapshot. No disk
        # backup file, no visible token contents and no source writes.
        source.backup(before, pages=4096, sleep=0.01)
        before.backup(candidate, pages=4096, sleep=0.01)
        initial = signature(before)
        if any("token_usage_source" in columns(before, t) for t in TABLES):
            raise ValueError("source_schema_already_has_provenance")
        apply_copy_migration(candidate)
        after = signature(candidate)
        checks = {
            "schema_changed_on_candidate": after["schema_sha256"] != initial["schema_sha256"],
            "all_old_rows_labeled_legacy": True,
            "tables_preserve_raw_counters": after["tables"] == initial["tables"],
            "all_new_columns_present": all(
                "token_usage_source" in columns(candidate, t) for t in TABLES),
        }
        for table in TABLES:
            count = candidate.execute(
                f"SELECT COUNT(*) FROM {table} WHERE token_usage_source='legacy_unverified'"
            ).fetchone()[0]
            checks["all_old_rows_labeled_legacy"] &= (count == initial["tables"][table]["rows"])
        # Idempotent second migration on the SAME copy.
        apply_copy_migration(candidate)
        checks["second_migration_is_noop"] = signature(candidate) == after

        # Simulate a rollback by restoring the original consistent snapshot,
        # *not* dropping a column via an unsafe live table rebuild.
        before.backup(candidate, pages=4096, sleep=0.01)
        restored = signature(candidate)
        checks["restore_recovered_original_schema_and_counters"] = restored == initial
        checks["source_schema_and_counters_unchanged"] = signature(source) == initial
        checks["source_query_only_confirmed"] = (
            source.execute("PRAGMA query_only").fetchone()[0] == 1)
        checks["restore_removed_added_columns"] = all(
            "token_usage_source" not in columns(candidate, t) for t in TABLES)
        return {
            "kind": "router-ledger-provenance-readonly-rollback-audit-v1",
            "mode": "read_only_source_in_memory_rehearsal",
            "status": "PASS" if all(checks.values()) else "HOLD",
            "checks": checks,
            "source_bytes_at_start": metadata.st_size,
            "source_tables": {
                table: {"rows": initial["tables"][table]["rows"],
                        "indexes": initial["tables"][table]["indexes"]}
                for table in TABLES
            },
            "source_schema_sha256": initial["schema_sha256"],
            "source_rows_stored_to_disk": False,
            "source_writes": 0,
            "production_approval_changed": False,
            "can_dispatch": False,
        }
    finally:
        if candidate is not None:
            candidate.close()
        if before is not None:
            before.close()
        source.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--max-db-bytes", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()
    try:
        result = audit(args.source_db, max_db_bytes=args.max_db_bytes)
    except (OSError, sqlite3.Error, ValueError) as exc:
        # Only safe static error identities, never paths or SQLite contents.
        print(json.dumps({"status": "HOLD", "reason_type": type(exc).__name__,
                          "source_writes": 0, "can_dispatch": False}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
