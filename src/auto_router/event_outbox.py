from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass
class OutboxEvent:
    event_type: str
    payload: dict[str, Any]
    idempotency_key: str
    source_service: str = "auto-router"
    event_id: str | None = None
    status: str = "pending"
    attempts: int = 0
    last_error: str | None = None
    created_at: int | None = None
    updated_at: int | None = None


class EventOutbox:
    """Durable event outbox for future AssistX/Neo4j write-back.

    Events are stored locally first so router work does not depend on AssistX
    being reachable. A later dispatcher can POST pending events to AssistX and
    mark them delivered or failed.
    """

    def __init__(self, database_url: str):
        self.database_path = self._path_from_url(database_url)
        self._in_memory = database_url == "sqlite:///:memory:"
        self._memory_connection: sqlite3.Connection | None = None
        if not self._in_memory:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def enqueue(self, event: OutboxEvent) -> str:
        event_id = event.event_id or str(uuid.uuid4())
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO event_outbox (
                    event_id, event_type, source_service, idempotency_key,
                    payload_json, status, attempts, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event.event_type,
                    event.source_service,
                    event.idempotency_key,
                    json.dumps(event.payload, sort_keys=True),
                    event.status,
                    event.attempts,
                    event.last_error,
                    event.created_at or now,
                    event.updated_at or now,
                ),
            )
            row = conn.execute(
                "SELECT event_id FROM event_outbox WHERE idempotency_key = ?",
                (event.idempotency_key,),
            ).fetchone()
        return str(row["event_id"] if row else event_id)

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, event_type, source_service, idempotency_key,
                       payload_json, status, attempts, last_error, created_at, updated_at
                FROM event_outbox
                WHERE status IN ('pending', 'retry')
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, event_type, source_service, idempotency_key,
                       payload_json, status, attempts, last_error, created_at, updated_at
                FROM event_outbox
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def mark_delivered(self, event_id: str) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE event_outbox
                SET status = 'delivered', updated_at = ?, last_error = NULL
                WHERE event_id = ?
                """,
                (now, event_id),
            )

    def mark_failed(self, event_id: str, error: str, retry: bool = True) -> None:
        now = int(time.time())
        status = "retry" if retry else "dead_letter"
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE event_outbox
                SET status = ?, attempts = attempts + 1, updated_at = ?, last_error = ?
                WHERE event_id = ?
                """,
                (status, now, error[:1000], event_id),
            )

    def summary(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM event_outbox
                GROUP BY status
                """
            ).fetchall()
        summary = {"pending": 0, "retry": 0, "delivered": 0, "dead_letter": 0}
        for row in rows:
            summary[str(row["status"])] = int(row["count"])
        summary["total"] = sum(summary.values())
        return summary

    def prune_terminal(self, keep_delivered: int = 1000, keep_dead_letter: int = 0) -> dict[str, int]:
        """Trim terminal outbox rows while preserving active backlog.

        Pending/retry rows are never removed here. Delivered and dead-letter rows
        are historical once the dispatch outcome is known, so keeping a bounded
        tail prevents the outbox from growing forever after a burst of failures.
        """
        with self._connect() as conn:
            delivered_deleted = 0
            dead_letter_deleted = 0
            if keep_delivered >= 0:
                delivered_deleted = int(
                    conn.execute(
                        """
                        DELETE FROM event_outbox
                        WHERE status = 'delivered'
                          AND id NOT IN (
                              SELECT id FROM event_outbox
                              WHERE status = 'delivered'
                              ORDER BY updated_at DESC, id DESC
                              LIMIT ?
                          )
                        """,
                        (keep_delivered,),
                    ).rowcount
                    or 0
                )
            if keep_dead_letter >= 0:
                dead_letter_deleted = int(
                    conn.execute(
                        """
                        DELETE FROM event_outbox
                        WHERE status = 'dead_letter'
                          AND id NOT IN (
                              SELECT id FROM event_outbox
                              WHERE status = 'dead_letter'
                              ORDER BY updated_at DESC, id DESC
                              LIMIT ?
                          )
                        """,
                        (keep_dead_letter,),
                    ).rowcount
                    or 0
                )
        return {"delivered_deleted": delivered_deleted, "dead_letter_deleted": dead_letter_deleted}

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "source_service": row["source_service"],
            "idempotency_key": row["idempotency_key"],
            "payload": json.loads(row["payload_json"] or "{}"),
            "status": row["status"],
            "attempts": row["attempts"],
            "last_error": row["last_error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _init_db(self) -> None:
        conn = self._connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                source_service TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER DEFAULT 0,
                last_error TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_outbox_status ON event_outbox(status, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_outbox_type ON event_outbox(event_type)"
        )
        if not self._in_memory:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        if self._in_memory:
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
            return self._memory_connection
        conn = sqlite3.connect(self.database_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
        except sqlite3.OperationalError:
            pass
        return conn

    def _path_from_url(self, database_url: str) -> Path:
        if database_url.startswith("sqlite:///./"):
            return Path(database_url.removeprefix("sqlite:///"))
        if database_url.startswith("sqlite:////"):
            parsed = urlparse(database_url)
            return Path(parsed.path)
        if database_url.startswith("sqlite:///"):
            return Path(database_url.removeprefix("sqlite:///"))
        return Path(database_url)
