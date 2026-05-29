from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class UsageEvent:
    request_id: str
    provider_id: str | None
    model_id: str | None
    route: str
    priority: str
    stage: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    quota_units: dict | None = None
    status_code: int | None = None
    latency_ms: int | None = None
    error_type: str | None = None
    error_message: str | None = None


class UsageLedger:
    """Durable SQLite usage ledger.

    The ledger stores routing metadata and usage counters. Prompt bodies are intentionally not
    persisted here; future debug logging should remain opt-in and redacted by default.
    """

    def __init__(self, database_url: str):
        self.database_path = self._path_from_url(database_url)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def record(self, event: UsageEvent) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_events (
                    request_id, provider_id, model_id, route, priority, stage,
                    input_tokens, output_tokens, total_tokens, quota_units_json,
                    status_code, latency_ms, error_type, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.request_id,
                    event.provider_id,
                    event.model_id,
                    event.route,
                    event.priority,
                    event.stage,
                    event.input_tokens,
                    event.output_tokens,
                    event.total_tokens,
                    json.dumps(event.quota_units or {}, sort_keys=True),
                    event.status_code,
                    event.latency_ms,
                    event.error_type,
                    event.error_message,
                    now,
                ),
            )

    def recent_events(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT request_id, provider_id, model_id, route, priority, stage,
                       input_tokens, output_tokens, total_tokens, status_code,
                       latency_ms, error_type, error_message, created_at
                FROM usage_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict:
        with self._connect() as conn:
            totals = conn.execute(
                """
                SELECT
                  COUNT(*) AS requests,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM usage_events
                """
            ).fetchone()
            by_provider = conn.execute(
                """
                SELECT provider_id, COUNT(*) AS requests, COALESCE(SUM(total_tokens), 0) AS tokens
                FROM usage_events
                GROUP BY provider_id
                ORDER BY requests DESC
                """
            ).fetchall()
        return {
            "totals": dict(totals) if totals else {},
            "by_provider": [dict(row) for row in by_provider],
        }

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    provider_id TEXT,
                    model_id TEXT,
                    route TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    stage TEXT,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    quota_units_json TEXT DEFAULT '{}',
                    status_code INTEGER,
                    latency_ms INTEGER,
                    error_type TEXT,
                    error_message TEXT,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_created_at ON usage_events(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_events(provider_id, model_id)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
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
