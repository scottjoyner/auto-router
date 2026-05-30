from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from auto_router.context import ServiceStatus
from auto_router.service_scanner import ServiceProbeResult


class ServiceStatusStore:
    """Durable SQLite store for service scan snapshots.

    This is intentionally small and independent from AssistX/Neo4j. It lets the
    router survive restarts while we later add outbox/write-back events for the
    graph-backed source of truth.
    """

    def __init__(self, database_url: str):
        self.database_path = self._path_from_url(database_url)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def save_results(self, results: list[ServiceProbeResult]) -> None:
        if not results:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO service_scan_events (
                    service_id, name, url, status, checked_at, latency_ms,
                    status_code, error, skipped, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        result.service_id,
                        result.name,
                        result.url,
                        result.status.value,
                        result.checked_at,
                        result.latency_ms,
                        result.status_code,
                        result.error,
                        1 if result.skipped else 0,
                        result.reason,
                    )
                    for result in results
                ],
            )

    def latest_results(self) -> list[ServiceProbeResult]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.service_id, e.name, e.url, e.status, e.checked_at,
                       e.latency_ms, e.status_code, e.error, e.skipped, e.reason
                FROM service_scan_events e
                JOIN (
                    SELECT service_id, MAX(checked_at) AS checked_at
                    FROM service_scan_events
                    GROUP BY service_id
                ) latest
                  ON e.service_id = latest.service_id
                 AND e.checked_at = latest.checked_at
                ORDER BY e.name
                """
            ).fetchall()
        return [self._row_to_result(row) for row in rows]

    def recent_results(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT service_id, name, url, status, checked_at, latency_ms,
                       status_code, error, skipped, reason
                FROM service_scan_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM (
                    SELECT e.service_id, e.status
                    FROM service_scan_events e
                    JOIN (
                        SELECT service_id, MAX(checked_at) AS checked_at
                        FROM service_scan_events
                        GROUP BY service_id
                    ) latest
                      ON e.service_id = latest.service_id
                     AND e.checked_at = latest.checked_at
                )
                GROUP BY status
                """
            ).fetchall()
        summary = {status.value: 0 for status in ServiceStatus}
        for row in rows:
            summary[str(row["status"])] = int(row["count"])
        summary["total"] = sum(summary.values())
        return summary

    def _row_to_result(self, row: sqlite3.Row) -> ServiceProbeResult:
        return ServiceProbeResult(
            service_id=row["service_id"],
            name=row["name"],
            url=row["url"],
            status=ServiceStatus(row["status"]),
            checked_at=int(row["checked_at"]),
            latency_ms=row["latency_ms"],
            status_code=row["status_code"],
            error=row["error"],
            skipped=bool(row["skipped"]),
            reason=row["reason"],
        )

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS service_scan_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    service_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    checked_at INTEGER NOT NULL,
                    latency_ms INTEGER,
                    status_code INTEGER,
                    error TEXT,
                    skipped INTEGER DEFAULT 0,
                    reason TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_service_scan_service ON service_scan_events(service_id, checked_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_service_scan_checked_at ON service_scan_events(checked_at)"
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
