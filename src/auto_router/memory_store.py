from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from auto_router.memory_models import (
    MemoryContext,
    MemoryIngestRequest,
    MemoryMatch,
    MemoryQuery,
    MemoryRecord,
)

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_./:-]+")


class DuplicateMemoryEventError(ValueError):
    pass


class MemoryStore:
    """Durable degraded-mode store and lexical retrieval implementation.

    AssistX/Neo4j remains the canonical cross-service memory authority. This
    store makes ingestion idempotent and keeps the router useful when that
    service is unavailable.
    """

    def __init__(self, database_url: str):
        self.database_path = self._path_from_url(database_url)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def ingest(self, request: MemoryIngestRequest) -> bool:
        payload = request.record.model_dump_json()
        stable_record = request.record.model_dump(mode="json", exclude={"created_at"})
        stable_payload = json.dumps(stable_record, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(
            f"{request.source}:{request.event_id}:{stable_payload}".encode()
        ).hexdigest()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT payload_hash FROM memory_events WHERE source=? AND event_id=?",
                (request.source, request.event_id),
            ).fetchone()
            if existing:
                if existing["payload_hash"] != digest:
                    raise DuplicateMemoryEventError(
                        "event_id already exists with different content"
                    )
                return False
            conn.execute(
                """
                INSERT INTO memory_events(source, event_id, payload_hash, memory_id, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    request.source,
                    request.event_id,
                    digest,
                    request.record.memory_id,
                    payload,
                ),
            )
            conn.execute(
                """
                INSERT INTO memories(memory_id, kind, repository, task_id, commit_sha,
                                     summary, confidence, active, tags, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    kind=excluded.kind,
                    repository=excluded.repository,
                    task_id=excluded.task_id,
                    commit_sha=excluded.commit_sha,
                    summary=excluded.summary,
                    confidence=excluded.confidence,
                    active=excluded.active,
                    tags=excluded.tags,
                    payload=excluded.payload
                """,
                (
                    request.record.memory_id,
                    request.record.kind.value,
                    request.record.repository,
                    request.record.task_id,
                    request.record.commit_sha,
                    request.record.summary,
                    request.record.confidence,
                    1 if request.record.active else 0,
                    json.dumps(request.record.tags),
                    payload,
                ),
            )
        return True

    def query(self, query: MemoryQuery) -> MemoryContext:
        clauses = ["active=1"]
        values: list[object] = []
        if query.repository:
            clauses.append("repository=?")
            values.append(query.repository)
        if query.task_id:
            clauses.append("task_id=?")
            values.append(query.task_id)
        if query.kinds:
            placeholders = ",".join("?" for _ in query.kinds)
            clauses.append(f"kind IN ({placeholders})")
            values.extend(kind.value for kind in query.kinds)
        sql = f"SELECT payload FROM memories WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            rows = conn.execute(sql, values).fetchall()

        query_terms = self._terms(query.query)
        matches: list[MemoryMatch] = []
        for row in rows:
            record = MemoryRecord.model_validate_json(row["payload"])
            score, reasons = self._score(record, query, query_terms)
            if score > 0:
                matches.append(MemoryMatch(record=record, score=score, reasons=reasons))
        matches.sort(
            key=lambda item: (
                item.score,
                item.record.successful_reuses,
                item.record.created_at,
            ),
            reverse=True,
        )
        matches = matches[: query.limit]
        text, estimated_tokens = self._render(matches, query.budget_tokens)
        return MemoryContext(
            query=query,
            matches=matches,
            context_text=text,
            estimated_tokens=estimated_tokens,
            backend="sqlite-lexical",
            degraded=True,
            warnings=[
                "Using router-local lexical memory; canonical AssistX/Neo4j retrieval was not used."
            ],
        )

    def summary(self) -> dict[str, object]:
        with self._connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) AS count FROM memories").fetchone()["count"])
            active = int(
                conn.execute("SELECT COUNT(*) AS count FROM memories WHERE active=1").fetchone()[
                    "count"
                ]
            )
            rows = conn.execute(
                "SELECT kind, COUNT(*) AS count FROM memories GROUP BY kind ORDER BY kind"
            ).fetchall()
        return {
            "backend": "sqlite-lexical",
            "total": total,
            "active": active,
            "by_kind": {str(row["kind"]): int(row["count"]) for row in rows},
        }

    def _score(
        self, record: MemoryRecord, query: MemoryQuery, query_terms: set[str]
    ) -> tuple[float, list[str]]:
        searchable = " ".join(
            [record.summary, " ".join(record.tags), record.repository or "", record.task_id or ""]
        )
        record_terms = self._terms(searchable)
        overlap = len(query_terms & record_terms)
        score = float(overlap) / max(len(query_terms), 1)
        reasons: list[str] = []
        if overlap:
            reasons.append(f"{overlap} lexical terms matched")
        if query.repository and record.repository == query.repository:
            score += 0.35
            reasons.append("repository matched")
        if query.task_id and record.task_id == query.task_id:
            score += 0.45
            reasons.append("task matched")
        if query.commit_sha and record.commit_sha == query.commit_sha:
            score += 0.2
            reasons.append("commit matched")
        tag_overlap = len(set(query.tags) & set(record.tags))
        if tag_overlap:
            score += min(tag_overlap * 0.1, 0.3)
            reasons.append(f"{tag_overlap} tags matched")
        score += record.confidence * 0.15
        score += min(record.successful_reuses * 0.03, 0.15)
        score -= min(record.contradictions * 0.15, 0.6)
        return max(score, 0.0), reasons

    def _render(self, matches: list[MemoryMatch], budget_tokens: int) -> tuple[str, int]:
        char_budget = budget_tokens * 4
        lines = ["Relevant fleet experience memory:"]
        for match in matches:
            evidence = ", ".join(item.reference for item in match.record.evidence[:3])
            line = (
                f"- [{match.record.kind.value}; confidence={match.record.confidence:.2f}] "
                f"{match.record.summary}"
            )
            if evidence:
                line += f" Evidence: {evidence}."
            candidate = "\n".join([*lines, line])
            if len(candidate) > char_budget:
                break
            lines.append(line)
        text = "\n".join(lines) if len(lines) > 1 else ""
        return text, (len(text) + 3) // 4

    @staticmethod
    def _terms(value: str) -> set[str]:
        return {term.lower() for term in _TOKEN_RE.findall(value) if len(term) > 2}

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source, event_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    repository TEXT,
                    task_id TEXT,
                    commit_sha TEXT,
                    summary TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    tags TEXT NOT NULL DEFAULT '[]',
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_repo_kind "
                "ON memories(repository, kind, active)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_task ON memories(task_id, active)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _path_from_url(database_url: str) -> Path:
        if database_url.startswith("sqlite:///./"):
            return Path(database_url.removeprefix("sqlite:///"))
        if database_url.startswith("sqlite:////"):
            return Path(urlparse(database_url).path)
        if database_url.startswith("sqlite:///"):
            return Path(database_url.removeprefix("sqlite:///"))
        return Path(database_url)
