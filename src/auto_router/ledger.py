from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any
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


@dataclass
class RuntimeSample:
    request_id: str
    provider_id: str | None
    model_id: str | None
    route: str
    priority: str
    stage: str | None
    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    queue_wait_ms: int | None = None
    load_time_ms: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    tokens_per_second: float | None = None
    value_units: int = 0
    value_per_second: float | None = None
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

    def record_runtime_sample(self, sample: RuntimeSample) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_samples (
                    request_id, provider_id, model_id, route, priority, stage,
                    started_at_ms, ended_at_ms, queue_wait_ms, load_time_ms,
                    input_tokens, output_tokens, total_tokens, tokens_per_second,
                    value_units, value_per_second, status_code, latency_ms,
                    error_type, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sample.request_id,
                    sample.provider_id,
                    sample.model_id,
                    sample.route,
                    sample.priority,
                    sample.stage,
                    sample.started_at_ms,
                    sample.ended_at_ms,
                    sample.queue_wait_ms,
                    sample.load_time_ms,
                    sample.input_tokens,
                    sample.output_tokens,
                    sample.total_tokens,
                    sample.tokens_per_second,
                    sample.value_units,
                    sample.value_per_second,
                    sample.status_code,
                    sample.latency_ms,
                    sample.error_type,
                    sample.error_message,
                    now,
                ),
            )
            self._complete_counterfactual(conn, sample)

    def record_counterfactual_decision(
        self,
        *,
        decision_id: str,
        request_id: str,
        stage: str,
        chosen: dict[str, Any],
        candidates: list[dict[str, Any]],
        rejections: list[str],
    ) -> None:
        enriched = [self._candidate_prediction(row) for row in candidates]
        chosen_prediction = next(
            (
                row for row in enriched
                if row.get("provider") == chosen.get("provider")
                and row.get("provider_model") == chosen.get("provider_model")
            ),
            self._candidate_prediction(chosen),
        )
        predicted_values = [
            float(row["predicted_value_per_hour"])
            for row in enriched
            if row.get("predicted_value_per_hour") is not None
        ]
        chosen_value = chosen_prediction.get("predicted_value_per_hour")
        predicted_regret = (
            max(predicted_values) - float(chosen_value)
            if predicted_values and chosen_value is not None else None
        )
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO counterfactual_decisions (
                    decision_id, request_id, stage, chosen_provider, chosen_model,
                    chosen_json, candidates_json, rejections_json,
                    predicted_regret, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'selected', ?, ?)
                """,
                (
                    decision_id,
                    request_id,
                    stage,
                    chosen.get("provider"),
                    chosen.get("provider_model") or chosen.get("model"),
                    json.dumps(chosen_prediction, sort_keys=True),
                    json.dumps(enriched, sort_keys=True),
                    json.dumps(rejections, sort_keys=True),
                    round(predicted_regret, 3) if predicted_regret is not None else None,
                    now,
                    now,
                ),
            )

    def counterfactual_summary(self, limit: int = 100) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT decision_id, request_id, stage, chosen_provider, chosen_model,
                       chosen_json, candidates_json, rejections_json,
                       predicted_regret, realized_value_per_hour, realized_regret,
                       status, latency_ms, error_type, created_at, updated_at
                FROM counterfactual_decisions
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 1000)),),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            for field in ("chosen_json", "candidates_json", "rejections_json"):
                raw = item.pop(field)
                fallback = "{}" if field == "chosen_json" else "[]"
                item[field.removesuffix("_json")] = json.loads(raw or fallback)
            items.append(item)
        completed = [row for row in items if row["status"] in {"completed", "failed"}]
        regrets = [float(row["realized_regret"]) for row in completed if row["realized_regret"] is not None]
        return {
            "summary": {
                "decisions": len(items),
                "completed": len(completed),
                "failed": sum(row["status"] == "failed" for row in completed),
                "avg_realized_regret": round(mean(regrets), 3) if regrets else None,
                "zero_regret_rate": (
                    round(sum(regret <= 0 for regret in regrets) / len(regrets), 3)
                    if regrets else None
                ),
            },
            "items": items,
        }

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

    def recent_runtime_samples(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT request_id, provider_id, model_id, route, priority, stage,
                       started_at_ms, ended_at_ms, queue_wait_ms, load_time_ms,
                       input_tokens, output_tokens, total_tokens, tokens_per_second,
                       value_units, value_per_second, status_code, latency_ms,
                       error_type, error_message, created_at
                FROM runtime_samples
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        samples = [dict(row) for row in rows]
        for sample in samples:
            sample["elapsed_ms"] = self._elapsed_ms(sample)
        return samples

    def runtime_summary(self, limit: int = 100) -> dict[str, object]:
        samples = self.recent_runtime_samples(limit=limit)
        by_provider: dict[str, list[dict[str, object]]] = {}
        for sample in samples:
            provider = str(sample.get("provider_id") or "unknown")
            by_provider.setdefault(provider, []).append(sample)
        return {
            "samples": len(samples),
            "successful": sum(1 for sample in samples if self._runtime_ok(sample)),
            "failed": sum(1 for sample in samples if not self._runtime_ok(sample)),
            "avg_latency_ms": self._runtime_avg(samples, "latency_ms"),
            "avg_elapsed_ms": self._runtime_avg(samples, "elapsed_ms"),
            "avg_queue_wait_ms": self._runtime_avg(samples, "queue_wait_ms"),
            "avg_load_time_ms": self._runtime_avg(samples, "load_time_ms"),
            "avg_tokens_per_second": self._runtime_avg(samples, "tokens_per_second"),
            "avg_value_per_second": self._runtime_avg(samples, "value_per_second"),
            "avg_value_units": self._runtime_avg(samples, "value_units"),
            "by_provider": [
                {
                    "provider": provider,
                    "provider_id": provider,
                    "samples": len(rows),
                    "successful": sum(1 for row in rows if self._runtime_ok(row)),
                    "failed": sum(1 for row in rows if not self._runtime_ok(row)),
                    "avg_latency_ms": self._runtime_avg(rows, "latency_ms"),
                    "avg_elapsed_ms": self._runtime_avg(rows, "elapsed_ms"),
                    "avg_queue_wait_ms": self._runtime_avg(rows, "queue_wait_ms"),
                    "avg_load_time_ms": self._runtime_avg(rows, "load_time_ms"),
                    "avg_tokens_per_second": self._runtime_avg(rows, "tokens_per_second"),
                    "avg_value_per_second": self._runtime_avg(rows, "value_per_second"),
                }
                for provider, rows in sorted(by_provider.items())
            ],
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_created_at ON usage_events(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_events(provider_id, model_id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    provider_id TEXT,
                    model_id TEXT,
                    route TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    stage TEXT,
                    started_at_ms INTEGER,
                    ended_at_ms INTEGER,
                    queue_wait_ms INTEGER,
                    load_time_ms INTEGER,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    tokens_per_second REAL,
                    value_units INTEGER DEFAULT 0,
                    value_per_second REAL,
                    status_code INTEGER,
                    latency_ms INTEGER,
                    error_type TEXT,
                    error_message TEXT,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runtime_created_at ON runtime_samples(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runtime_provider ON runtime_samples(provider_id, model_id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS counterfactual_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    chosen_provider TEXT,
                    chosen_model TEXT,
                    chosen_json TEXT NOT NULL DEFAULT '{}',
                    candidates_json TEXT NOT NULL DEFAULT '[]',
                    rejections_json TEXT NOT NULL DEFAULT '[]',
                    predicted_regret REAL,
                    realized_value_per_hour REAL,
                    realized_regret REAL,
                    status TEXT NOT NULL DEFAULT 'selected',
                    latency_ms INTEGER,
                    error_type TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_counterfactual_request "
                "ON counterfactual_decisions(request_id, created_at)"
            )

    def prune(self, keep: int = 5000) -> None:
        """Bound the usage/runtime history so the tables can't grow without
        limit (every routed request writes a row; unbounded growth makes each
        blocking sqlite write slow and starves the async event loop)."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM usage_events WHERE id NOT IN ("
                "SELECT id FROM usage_events ORDER BY id DESC LIMIT ?)",
                (keep,),
            )
            conn.execute(
                "DELETE FROM runtime_samples WHERE id NOT IN ("
                "SELECT id FROM runtime_samples ORDER BY id DESC LIMIT ?)",
                (keep,),
            )
            conn.execute(
                "DELETE FROM counterfactual_decisions WHERE id NOT IN ("
                "SELECT id FROM counterfactual_decisions ORDER BY id DESC LIMIT ?)",
                (keep,),
            )

    def _candidate_prediction(self, candidate: dict[str, Any]) -> dict[str, Any]:
        provider = str(candidate.get("provider") or "")
        model = str(candidate.get("provider_model") or candidate.get("model") or "")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT AVG(latency_ms) AS latency_ms,
                       AVG(value_per_second) * 3600.0 AS value_per_hour,
                       AVG(CASE WHEN error_type IS NULL
                                 AND (status_code IS NULL OR status_code < 400)
                                THEN 1.0 ELSE 0.0 END) AS success_rate,
                       COUNT(*) AS samples
                FROM runtime_samples
                WHERE provider_id=? AND model_id=?
                """,
                (provider, model),
            ).fetchone()
        result = dict(candidate)
        result.update({
            "predicted_latency_ms": round(float(row["latency_ms"]), 1) if row and row["latency_ms"] is not None else None,
            "predicted_value_per_hour": round(float(row["value_per_hour"]), 3) if row and row["value_per_hour"] is not None else None,
            "predicted_success_rate": round(float(row["success_rate"]), 3) if row and row["success_rate"] is not None else None,
            "prediction_samples": int(row["samples"] or 0) if row else 0,
        })
        return result

    def _complete_counterfactual(self, conn: sqlite3.Connection, sample: RuntimeSample) -> None:
        row = conn.execute(
            """
            SELECT id, candidates_json
            FROM counterfactual_decisions
            WHERE request_id=? AND chosen_provider=? AND chosen_model=?
              AND status='selected'
            ORDER BY id DESC LIMIT 1
            """,
            (sample.request_id, sample.provider_id, sample.model_id),
        ).fetchone()
        if row is None:
            return
        candidates = json.loads(row["candidates_json"] or "[]")
        alternatives = [
            float(item["predicted_value_per_hour"])
            for item in candidates
            if item.get("predicted_value_per_hour") is not None
        ]
        realized = (
            float(sample.value_per_second) * 3600.0
            if sample.value_per_second is not None else None
        )
        regret = max(alternatives) - realized if alternatives and realized is not None else None
        ok = not sample.error_type and (
            sample.status_code is None or 200 <= int(sample.status_code) < 400
        )
        conn.execute(
            """
            UPDATE counterfactual_decisions
            SET realized_value_per_hour=?, realized_regret=?, status=?,
                latency_ms=?, error_type=?, updated_at=?
            WHERE id=?
            """,
            (
                round(realized, 3) if realized is not None else None,
                round(regret, 3) if regret is not None else None,
                "completed" if ok else "failed",
                sample.latency_ms,
                sample.error_type,
                int(time.time()),
                row["id"],
            ),
        )

    def _connect(self) -> sqlite3.Connection:
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

    def _runtime_ok(self, sample: dict[str, Any]) -> bool:
        if sample.get("error_type") or sample.get("error_message"):
            return False
        status_code = self._coerce_int(sample.get("status_code"))
        return status_code is None or 200 <= status_code < 400

    def _runtime_avg(self, rows: list[dict[str, Any]], field: str) -> int | float:
        values: list[float] = []
        for row in rows:
            if field == "elapsed_ms":
                elapsed = self._elapsed_ms(row)
                if elapsed is not None:
                    values.append(float(elapsed))
                continue
            value = row.get(field)
            if value is None:
                continue
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                continue
        if not values:
            return 0
        if field.endswith("_ms") or field == "value_units":
            return int(round(mean(values)))
        return round(mean(values), 3)

    def _elapsed_ms(self, row: dict[str, Any]) -> int | None:
        started = self._coerce_int(row.get("started_at_ms"))
        ended = self._coerce_int(row.get("ended_at_ms"))
        if started is None or ended is None:
            latency_ms = self._coerce_int(row.get("latency_ms"))
            return latency_ms
        return max(ended - started, 0)

    def _coerce_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
