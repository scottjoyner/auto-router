from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class UsageLedger:
    def __init__(self, database_url: str):
        self.path = self._parse_path(database_url)
        self._init_db()

    def _parse_path(self, url: str) -> Path:
        if url.startswith("sqlite:///"):
            return Path(url[10:])
        return Path("data/router.sqlite3")

    def _init_db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage_events (
                    id TEXT PRIMARY KEY,
                    request_id TEXT,
                    provider TEXT,
                    model TEXT,
                    route TEXT,
                    stage TEXT,
                    profile TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    status_code INTEGER,
                    latency_ms INTEGER,
                    created_at INTEGER
                )
            """)

    def record(
        self,
        request_id: str,
        provider: str,
        model: str,
        route: str,
        stage: str,
        profile: str,
        usage: dict[str, int],
        status_code: int = 200,
        latency_ms: int = 0,
    ):
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO usage_events (
                    id, request_id, provider, model, route, stage, profile,
                    input_tokens, output_tokens, status_code, latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"evt_{int(time.time())}_{request_id[:8]}",
                    request_id,
                    provider,
                    model,
                    route,
                    stage,
                    profile,
                    usage.get("prompt_tokens", usage.get("input_tokens", 0)),
                    usage.get("completion_tokens", usage.get("output_tokens", 0)),
                    status_code,
                    latency_ms,
                    int(time.time()),
                ),
            )

    def get_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM usage_events ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]
