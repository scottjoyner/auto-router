#!/usr/bin/env python3
"""RESEARCH-ONLY SQLite handoff journal for fleet-exec protocol exploration.

This is a *single-file laboratory model*, not a drop-in production relay.
SQLite local locking does NOT guarantee cross-host exclusivity, authority,
tamper resistance, independent custody, no duplicate external side effects,
nor rollback prevention. No networking, subprocess, shell or remote execution.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SYNTHETIC_ACK = "SYNTHETIC_TEST_ONLY_NO_PRODUCTION_APPROVAL"
HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
IDENTITY = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}\Z")
NONTERMINAL = ("QUEUED", "DELIVERED", "ACKED")
TERMINAL = ("RESULT_RECORDED", "QUARANTINED")


class Denied(ValueError):
    """Research journal refuses this transition; never execute the task."""


def _valid_identity(value: str) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise Denied("invalid identity")
    return value


def _valid_digest(value: str) -> str:
    if not isinstance(value, str) or not HEX_SHA256.fullmatch(value):
        raise Denied("digest must be 64 lowercase hex characters")
    return value


class HandoffJournal:
    def __init__(self, path: Path):
        path = Path(path)
        if not path.is_absolute() or str(path) == ":memory:" or path.is_symlink():
            raise Denied("explicit non-symlink absolute research database required")
        if not path.parent.is_dir():
            raise Denied("research directory does not exist")
        self.db = sqlite3.connect(path, timeout=2, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=2000")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS controller (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                epoch INTEGER NOT NULL CHECK(epoch>=0),
                owner TEXT NOT NULL
            );
            INSERT OR IGNORE INTO controller VALUES (1, 0, 'none');
            CREATE TABLE IF NOT EXISTS calls (
                call_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN
                    ('QUEUED','DELIVERED','ACKED','RESULT_RECORDED','QUARANTINED')),
                result_digest TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                owner TEXT NOT NULL,
                kind TEXT NOT NULL,
                FOREIGN KEY(call_id) REFERENCES calls(call_id)
            );
            """
        )

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    @staticmethod
    def _assert_owner(db: sqlite3.Connection, owner: str, epoch: int) -> None:
        row = db.execute("SELECT epoch, owner FROM controller WHERE singleton=1").fetchone()
        if row != (epoch, owner):
            raise Denied("stale, unknown or conflicting controller epoch")

    def synthetic_epoch(self, owner: str, epoch: int, acknowledgement: str) -> int:
        """Research-only promotion; refuses all real authority tokens.

        Explicitly quarantines all unfinished prior-epoch calls. This
        deliberately does not recover or automatically replay side effects.
        """
        _valid_identity(owner)
        if acknowledgement != SYNTHETIC_ACK or not isinstance(epoch, int) or epoch <= 0:
            raise Denied("not an externally authorized promotion")
        with self._tx() as db:
            current_epoch, _ = db.execute(
                "SELECT epoch,owner FROM controller WHERE singleton=1"
            ).fetchone()
            if epoch <= current_epoch:
                raise Denied("monotonic epoch required, no same-epoch takeover")
            affected = db.execute(
                "SELECT call_id,epoch FROM calls WHERE state IN ('QUEUED','DELIVERED','ACKED')"
            ).fetchall()
            db.execute(
                "UPDATE calls SET state='QUARANTINED' WHERE state IN ('QUEUED','DELIVERED','ACKED')"
            )
            db.execute("UPDATE controller SET epoch=?,owner=? WHERE singleton=1", (epoch, owner))
            for call_id, _ in affected:
                db.execute(
                    "INSERT INTO events (call_id,epoch,owner,kind) VALUES (?,?,?,?)",
                    (call_id, epoch, owner, "QUARANTINED_UNCERTAIN_PRIOR_EPOCH"),
                )
        return len(affected)

    def enqueue(
        self, owner: str, epoch: int, call_id: str, device_id: str, payload_digest: str
    ) -> str:
        _valid_identity(owner)
        _valid_identity(call_id)
        _valid_identity(device_id)
        _valid_digest(payload_digest)
        with self._tx() as db:
            self._assert_owner(db, owner, epoch)
            row = db.execute(
                "SELECT device_id,payload_digest,epoch,state FROM calls WHERE call_id=?",
                (call_id,),
            ).fetchone()
            if row is not None:
                if row[:3] != (device_id, payload_digest, epoch):
                    raise Denied("call_id reuse with different content or epoch")
                return row[3]  # Duplicate identical request never dispatched again.
            db.execute(
                "INSERT INTO calls VALUES (?,?,?,?,?,NULL)",
                (call_id, device_id, payload_digest, epoch, "QUEUED"),
            )
            self._event(db, call_id, epoch, owner, "QUEUED")
            return "QUEUED"

    @staticmethod
    def _event(db: sqlite3.Connection, call_id: str, epoch: int, owner: str, kind: str) -> None:
        db.execute(
            "INSERT INTO events (call_id,epoch,owner,kind) VALUES (?,?,?,?)",
            (call_id, epoch, owner, kind),
        )

    def advance(
        self,
        owner: str,
        epoch: int,
        call_id: str,
        device_id: str,
        transition: str,
        result_digest: str | None = None,
    ) -> str:
        _valid_identity(owner)
        _valid_identity(call_id)
        _valid_identity(device_id)
        if transition not in ("DELIVERED", "ACKED", "RESULT_RECORDED"):
            raise Denied("unsupported transition")
        if transition == "RESULT_RECORDED":
            _valid_digest(result_digest)
        elif result_digest is not None:
            raise Denied("unexpected result digest")
        with self._tx() as db:
            self._assert_owner(db, owner, epoch)
            row = db.execute(
                "SELECT device_id,epoch,state,result_digest FROM calls WHERE call_id=?",
                (call_id,),
            ).fetchone()
            if row is None or row[0] != device_id or row[1] != epoch:
                raise Denied("unknown, cross-device or cross-epoch call")
            previous, prior_digest = row[2], row[3]
            if previous == transition:
                if transition == "RESULT_RECORDED" and result_digest != prior_digest:
                    raise Denied("conflicting result for same call")
                return previous
            expected = {
                "DELIVERED": "QUEUED",
                "ACKED": "DELIVERED",
                "RESULT_RECORDED": "ACKED",
            }[transition]
            if previous != expected:
                raise Denied("out-of-order, terminal or unsafe replay transition")
            db.execute(
                "UPDATE calls SET state=?,result_digest=? WHERE call_id=?",
                (transition, result_digest, call_id),
            )
            self._event(db, call_id, epoch, owner, transition)
        return transition

    def snapshot(self) -> dict:
        controller = self.db.execute(
            "SELECT epoch,owner FROM controller WHERE singleton=1"
        ).fetchone()
        calls = self.db.execute(
            "SELECT call_id,device_id,epoch,state FROM calls ORDER BY call_id"
        ).fetchall()
        counts = self.db.execute(
            "SELECT state,count(*) FROM calls GROUP BY state ORDER BY state"
        ).fetchall()
        return {
            "research_only": True,
            "production_fencing_proven": False,
            "external_custody_proven": False,
            "at_most_once_external_effects_proven": False,
            "controller": {"epoch": controller[0], "owner": controller[1]},
            "calls": [
                {"call_id": c, "device_id": d, "epoch": e, "state": s} for c, d, e, s in calls
            ],
            "counts": dict(counts),
            "event_count": self.db.execute("SELECT count(*) FROM events").fetchone()[0],
        }


if __name__ == "__main__":
    print(json.dumps({"research_only": True, "no_cli_dispatch_or_cutover": True}))
