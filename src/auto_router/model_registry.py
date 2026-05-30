from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

from auto_router.live_models import LiveModelSnapshot


class ModelRegistryStore:
    """Durable provider model registry.

    Live `/models` refreshes are volatile. This store keeps the latest discovered
    provider inventory so the dashboard and AssistX/Neo4j projection can survive
    restarts and compare provider drift over time.
    """

    def __init__(self, database_url: str):
        self.database_path = self._path_from_url(database_url)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def save_snapshot(self, snapshot: LiveModelSnapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO model_registry_snapshots (
                    provider, ok, fetched_at, expires_at, model_count, error, models_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.provider,
                    1 if snapshot.ok else 0,
                    snapshot.fetched_at,
                    snapshot.expires_at,
                    len(snapshot.models),
                    snapshot.error,
                    json.dumps(snapshot.models, sort_keys=True),
                ),
            )

    def latest_snapshots(self) -> list[LiveModelSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.provider, s.ok, s.fetched_at, s.expires_at,
                       s.models_json, s.error
                FROM model_registry_snapshots s
                JOIN (
                    SELECT provider, MAX(fetched_at) AS fetched_at
                    FROM model_registry_snapshots
                    GROUP BY provider
                ) latest
                  ON s.provider = latest.provider
                 AND s.fetched_at = latest.fetched_at
                ORDER BY s.provider
                """
            ).fetchall()
        return [self._row_to_snapshot(row) for row in rows]

    def latest_for_provider(self, provider: str) -> LiveModelSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT provider, ok, fetched_at, expires_at, models_json, error
                FROM model_registry_snapshots
                WHERE provider = ?
                ORDER BY fetched_at DESC, id DESC
                LIMIT 1
                """,
                (provider,),
            ).fetchone()
        return self._row_to_snapshot(row) if row else None

    def recent_snapshots(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT provider, ok, fetched_at, expires_at, model_count, error
                FROM model_registry_snapshots
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict[str, int]:
        latest = self.latest_snapshots()
        return {
            "providers": len(latest),
            "ok": sum(1 for snapshot in latest if snapshot.ok),
            "error": sum(1 for snapshot in latest if not snapshot.ok),
            "models": sum(len(snapshot.models) for snapshot in latest),
            "stale": sum(1 for snapshot in latest if snapshot.stale),
        }

    def _row_to_snapshot(self, row: sqlite3.Row) -> LiveModelSnapshot:
        return LiveModelSnapshot(
            provider=row["provider"],
            ok=bool(row["ok"]),
            fetched_at=int(row["fetched_at"]),
            expires_at=int(row["expires_at"]),
            models=json.loads(row["models_json"] or "[]"),
            error=row["error"],
        )

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS model_registry_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    model_count INTEGER NOT NULL,
                    error TEXT,
                    models_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_registry_provider ON model_registry_snapshots(provider, fetched_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_registry_fetched ON model_registry_snapshots(fetched_at)"
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
