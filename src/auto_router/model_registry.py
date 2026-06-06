from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from statistics import mean
from urllib.parse import urlparse

from auto_router.live_models import LiveModelSnapshot


class ModelRegistryStore:
    """Durable provider model registry and probe history."""

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

    def save_probe(
        self,
        snapshot: LiveModelSnapshot,
        latency_ms: int | None = None,
        previous_snapshot: LiveModelSnapshot | None = None,
    ) -> dict[str, object]:
        signature = self._snapshot_signature(snapshot.models)
        previous_signature = self._snapshot_signature(previous_snapshot.models) if previous_snapshot is not None else None
        drift = bool(snapshot.ok and previous_signature and signature != previous_signature)
        changed_models = self._changed_models(previous_snapshot.models, snapshot.models) if previous_snapshot is not None else []
        probe = {
            "provider": snapshot.provider,
            "ok": snapshot.ok,
            "fetched_at": snapshot.fetched_at,
            "expires_at": snapshot.expires_at,
            "latency_ms": latency_ms,
            "model_count": len(snapshot.models),
            "drift": drift,
            "signature": signature,
            "previous_signature": previous_signature,
            "error": snapshot.error,
            "changed_models": changed_models,
            "models": snapshot.models,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO model_registry_probes (
                    provider, ok, fetched_at, expires_at, latency_ms, model_count,
                    drift, signature, previous_signature, error, models_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.provider,
                    1 if snapshot.ok else 0,
                    snapshot.fetched_at,
                    snapshot.expires_at,
                    latency_ms,
                    len(snapshot.models),
                    1 if drift else 0,
                    signature,
                    previous_signature,
                    snapshot.error,
                    json.dumps(snapshot.models, sort_keys=True),
                ),
            )
        snapshot.latency_ms = latency_ms
        snapshot.drift = drift
        snapshot.signature = signature
        snapshot.previous_signature = previous_signature
        snapshot.changed_models = changed_models
        return probe

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
        probe = self.latest_probe_for_provider(provider)
        if probe is not None:
            return probe
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

    def latest_probes(self) -> list[LiveModelSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.provider, p.ok, p.fetched_at, p.expires_at,
                       p.models_json, p.error, p.latency_ms, p.drift,
                       p.signature, p.previous_signature
                FROM model_registry_probes p
                JOIN (
                    SELECT provider, MAX(fetched_at) AS fetched_at
                    FROM model_registry_probes
                    GROUP BY provider
                ) latest
                  ON p.provider = latest.provider
                 AND p.fetched_at = latest.fetched_at
                ORDER BY p.provider
                """
            ).fetchall()
        return [self._row_to_probe(row) for row in rows]

    def latest_probe_for_provider(self, provider: str) -> LiveModelSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT provider, ok, fetched_at, expires_at, models_json, error,
                       latency_ms, drift, signature, previous_signature
                FROM model_registry_probes
                WHERE provider = ?
                ORDER BY fetched_at DESC, id DESC
                LIMIT 1
                """,
                (provider,),
            ).fetchone()
        return self._row_to_probe(row) if row else None

    def latest_inventory(self) -> list[LiveModelSnapshot]:
        inventory = {snapshot.provider: snapshot for snapshot in self.latest_snapshots()}
        for probe in self.latest_probes():
            inventory[probe.provider] = probe
        return sorted(inventory.values(), key=lambda item: item.provider)

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

    def recent_probes(self, limit: int = 100) -> list[dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT provider, ok, fetched_at, expires_at, latency_ms, model_count,
                       drift, signature, previous_signature, error
                FROM model_registry_probes
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "provider": row["provider"],
                "ok": bool(row["ok"]),
                "fetched_at": int(row["fetched_at"]),
                "expires_at": int(row["expires_at"]),
                "latency_ms": row["latency_ms"],
                "model_count": int(row["model_count"]),
                "drift": bool(row["drift"]),
                "signature": row["signature"],
                "previous_signature": row["previous_signature"],
                "error": row["error"],
            }
            for row in rows
        ]

    def summary(self) -> dict[str, int]:
        latest = self.latest_inventory()
        return {
            "providers": len(latest),
            "ok": sum(1 for snapshot in latest if snapshot.ok),
            "error": sum(1 for snapshot in latest if not snapshot.ok),
            "models": sum(len(snapshot.models) for snapshot in latest),
            "stale": sum(1 for snapshot in latest if snapshot.stale),
        }

    def probe_summary(self) -> dict[str, int]:
        latest = self.latest_probes()
        return {
            "providers": len(latest),
            "ok": sum(1 for snapshot in latest if snapshot.ok),
            "error": sum(1 for snapshot in latest if not snapshot.ok),
            "models": sum(len(snapshot.models) for snapshot in latest),
            "drift": sum(1 for snapshot in latest if snapshot.drift),
            "healthy": sum(1 for snapshot in latest if snapshot.ok and not snapshot.drift and len(snapshot.models) > 0),
            "avg_latency_ms": int(mean([snapshot.latency_ms for snapshot in latest if snapshot.latency_ms is not None]) if any(snapshot.latency_ms is not None for snapshot in latest) else 0),
        }

    def provider_health_reports(self, window: int = 10) -> list[dict[str, object]]:
        recent = self.recent_probes(limit=max(window * 10, 50))
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in recent:
            grouped.setdefault(str(row["provider"]), []).append(row)
        reports: list[dict[str, object]] = []
        now = int(time.time())
        for provider, rows in grouped.items():
            window_rows = rows[:window]
            latest = window_rows[0]
            score = self._health_score(window_rows, now=now)
            reports.append(
                {
                    "provider": provider,
                    "health_score": score,
                    "ok": bool(latest["ok"]),
                    "drift": any(bool(row["drift"]) for row in window_rows),
                    "model_count": int(latest["model_count"]),
                    "latency_ms": latest["latency_ms"],
                    "last_fetched_at": int(latest["fetched_at"]),
                    "age_seconds": max(now - int(latest["fetched_at"]), 0),
                    "success_rate": round(sum(1 for row in window_rows if row["ok"]) / len(window_rows), 3),
                    "error": latest["error"],
                    "signature": latest["signature"],
                    "previous_signature": latest["previous_signature"],
                    "recent": window_rows,
                }
            )
        return sorted(reports, key=lambda item: (-int(item["health_score"]), str(item["provider"])))

    def _health_score(self, rows: list[dict[str, object]], now: int | None = None) -> int:
        if not rows:
            return 0
        now = now or int(time.time())
        success_rate = sum(1 for row in rows if row["ok"]) / len(rows)
        latest = rows[0]
        age_seconds = max(now - int(latest["fetched_at"]), 0)
        freshness = max(0.0, 1.0 - min(age_seconds, 3600) / 3600)
        model_presence = 1.0 if int(latest["model_count"]) > 0 else 0.0
        latencies = [int(row["latency_ms"]) for row in rows if row["latency_ms"] is not None]
        if latencies:
            latency_score = max(0.0, 1.0 - min(mean(latencies), 5000) / 5000)
        else:
            latency_score = 0.5
        drift_penalty = 0.15 if any(bool(row["drift"]) for row in rows) else 0.0
        score = 100 * (0.5 * success_rate + 0.2 * freshness + 0.2 * model_presence + 0.1 * latency_score) - (100 * drift_penalty)
        return max(min(int(round(score)), 100), 0)

    def _row_to_snapshot(self, row: sqlite3.Row) -> LiveModelSnapshot:
        return LiveModelSnapshot(
            provider=row["provider"],
            ok=bool(row["ok"]),
            fetched_at=int(row["fetched_at"]),
            expires_at=int(row["expires_at"]),
            models=json.loads(row["models_json"] or "[]"),
            error=row["error"],
        )

    def _row_to_probe(self, row: sqlite3.Row) -> LiveModelSnapshot:
        return LiveModelSnapshot(
            provider=row["provider"],
            ok=bool(row["ok"]),
            fetched_at=int(row["fetched_at"]),
            expires_at=int(row["expires_at"]),
            models=json.loads(row["models_json"] or "[]"),
            error=row["error"],
            latency_ms=row["latency_ms"],
            drift=bool(row["drift"]),
            signature=row["signature"],
            previous_signature=row["previous_signature"],
        )

    def _snapshot_signature(self, models: list[dict[str, object]]) -> str:
        canonical = [self._model_identity(model) for model in models]
        payload = json.dumps(
            sorted(canonical, key=lambda item: (item["id"], item["owned_by"], item["object"])),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _changed_models(self, previous_models: list[dict[str, object]], current_models: list[dict[str, object]]) -> list[str]:
        previous_ids = {self._model_identity(model)["id"] for model in previous_models}
        current_ids = {self._model_identity(model)["id"] for model in current_models}
        return sorted((previous_ids ^ current_ids) - {""})

    def _model_identity(self, model: dict[str, object]) -> dict[str, str]:
        model_id = str(model.get("id") or model.get("name") or model.get("model") or "").strip()
        return {
            "id": model_id,
            "owned_by": str(model.get("owned_by") or model.get("owner") or "").strip(),
            "object": str(model.get("object") or "model").strip(),
        }

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS model_registry_probes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    latency_ms INTEGER,
                    model_count INTEGER NOT NULL,
                    drift INTEGER NOT NULL DEFAULT 0,
                    signature TEXT,
                    previous_signature TEXT,
                    error TEXT,
                    models_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_registry_probe_provider ON model_registry_probes(provider, fetched_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_registry_probe_fetched ON model_registry_probes(fetched_at)"
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
