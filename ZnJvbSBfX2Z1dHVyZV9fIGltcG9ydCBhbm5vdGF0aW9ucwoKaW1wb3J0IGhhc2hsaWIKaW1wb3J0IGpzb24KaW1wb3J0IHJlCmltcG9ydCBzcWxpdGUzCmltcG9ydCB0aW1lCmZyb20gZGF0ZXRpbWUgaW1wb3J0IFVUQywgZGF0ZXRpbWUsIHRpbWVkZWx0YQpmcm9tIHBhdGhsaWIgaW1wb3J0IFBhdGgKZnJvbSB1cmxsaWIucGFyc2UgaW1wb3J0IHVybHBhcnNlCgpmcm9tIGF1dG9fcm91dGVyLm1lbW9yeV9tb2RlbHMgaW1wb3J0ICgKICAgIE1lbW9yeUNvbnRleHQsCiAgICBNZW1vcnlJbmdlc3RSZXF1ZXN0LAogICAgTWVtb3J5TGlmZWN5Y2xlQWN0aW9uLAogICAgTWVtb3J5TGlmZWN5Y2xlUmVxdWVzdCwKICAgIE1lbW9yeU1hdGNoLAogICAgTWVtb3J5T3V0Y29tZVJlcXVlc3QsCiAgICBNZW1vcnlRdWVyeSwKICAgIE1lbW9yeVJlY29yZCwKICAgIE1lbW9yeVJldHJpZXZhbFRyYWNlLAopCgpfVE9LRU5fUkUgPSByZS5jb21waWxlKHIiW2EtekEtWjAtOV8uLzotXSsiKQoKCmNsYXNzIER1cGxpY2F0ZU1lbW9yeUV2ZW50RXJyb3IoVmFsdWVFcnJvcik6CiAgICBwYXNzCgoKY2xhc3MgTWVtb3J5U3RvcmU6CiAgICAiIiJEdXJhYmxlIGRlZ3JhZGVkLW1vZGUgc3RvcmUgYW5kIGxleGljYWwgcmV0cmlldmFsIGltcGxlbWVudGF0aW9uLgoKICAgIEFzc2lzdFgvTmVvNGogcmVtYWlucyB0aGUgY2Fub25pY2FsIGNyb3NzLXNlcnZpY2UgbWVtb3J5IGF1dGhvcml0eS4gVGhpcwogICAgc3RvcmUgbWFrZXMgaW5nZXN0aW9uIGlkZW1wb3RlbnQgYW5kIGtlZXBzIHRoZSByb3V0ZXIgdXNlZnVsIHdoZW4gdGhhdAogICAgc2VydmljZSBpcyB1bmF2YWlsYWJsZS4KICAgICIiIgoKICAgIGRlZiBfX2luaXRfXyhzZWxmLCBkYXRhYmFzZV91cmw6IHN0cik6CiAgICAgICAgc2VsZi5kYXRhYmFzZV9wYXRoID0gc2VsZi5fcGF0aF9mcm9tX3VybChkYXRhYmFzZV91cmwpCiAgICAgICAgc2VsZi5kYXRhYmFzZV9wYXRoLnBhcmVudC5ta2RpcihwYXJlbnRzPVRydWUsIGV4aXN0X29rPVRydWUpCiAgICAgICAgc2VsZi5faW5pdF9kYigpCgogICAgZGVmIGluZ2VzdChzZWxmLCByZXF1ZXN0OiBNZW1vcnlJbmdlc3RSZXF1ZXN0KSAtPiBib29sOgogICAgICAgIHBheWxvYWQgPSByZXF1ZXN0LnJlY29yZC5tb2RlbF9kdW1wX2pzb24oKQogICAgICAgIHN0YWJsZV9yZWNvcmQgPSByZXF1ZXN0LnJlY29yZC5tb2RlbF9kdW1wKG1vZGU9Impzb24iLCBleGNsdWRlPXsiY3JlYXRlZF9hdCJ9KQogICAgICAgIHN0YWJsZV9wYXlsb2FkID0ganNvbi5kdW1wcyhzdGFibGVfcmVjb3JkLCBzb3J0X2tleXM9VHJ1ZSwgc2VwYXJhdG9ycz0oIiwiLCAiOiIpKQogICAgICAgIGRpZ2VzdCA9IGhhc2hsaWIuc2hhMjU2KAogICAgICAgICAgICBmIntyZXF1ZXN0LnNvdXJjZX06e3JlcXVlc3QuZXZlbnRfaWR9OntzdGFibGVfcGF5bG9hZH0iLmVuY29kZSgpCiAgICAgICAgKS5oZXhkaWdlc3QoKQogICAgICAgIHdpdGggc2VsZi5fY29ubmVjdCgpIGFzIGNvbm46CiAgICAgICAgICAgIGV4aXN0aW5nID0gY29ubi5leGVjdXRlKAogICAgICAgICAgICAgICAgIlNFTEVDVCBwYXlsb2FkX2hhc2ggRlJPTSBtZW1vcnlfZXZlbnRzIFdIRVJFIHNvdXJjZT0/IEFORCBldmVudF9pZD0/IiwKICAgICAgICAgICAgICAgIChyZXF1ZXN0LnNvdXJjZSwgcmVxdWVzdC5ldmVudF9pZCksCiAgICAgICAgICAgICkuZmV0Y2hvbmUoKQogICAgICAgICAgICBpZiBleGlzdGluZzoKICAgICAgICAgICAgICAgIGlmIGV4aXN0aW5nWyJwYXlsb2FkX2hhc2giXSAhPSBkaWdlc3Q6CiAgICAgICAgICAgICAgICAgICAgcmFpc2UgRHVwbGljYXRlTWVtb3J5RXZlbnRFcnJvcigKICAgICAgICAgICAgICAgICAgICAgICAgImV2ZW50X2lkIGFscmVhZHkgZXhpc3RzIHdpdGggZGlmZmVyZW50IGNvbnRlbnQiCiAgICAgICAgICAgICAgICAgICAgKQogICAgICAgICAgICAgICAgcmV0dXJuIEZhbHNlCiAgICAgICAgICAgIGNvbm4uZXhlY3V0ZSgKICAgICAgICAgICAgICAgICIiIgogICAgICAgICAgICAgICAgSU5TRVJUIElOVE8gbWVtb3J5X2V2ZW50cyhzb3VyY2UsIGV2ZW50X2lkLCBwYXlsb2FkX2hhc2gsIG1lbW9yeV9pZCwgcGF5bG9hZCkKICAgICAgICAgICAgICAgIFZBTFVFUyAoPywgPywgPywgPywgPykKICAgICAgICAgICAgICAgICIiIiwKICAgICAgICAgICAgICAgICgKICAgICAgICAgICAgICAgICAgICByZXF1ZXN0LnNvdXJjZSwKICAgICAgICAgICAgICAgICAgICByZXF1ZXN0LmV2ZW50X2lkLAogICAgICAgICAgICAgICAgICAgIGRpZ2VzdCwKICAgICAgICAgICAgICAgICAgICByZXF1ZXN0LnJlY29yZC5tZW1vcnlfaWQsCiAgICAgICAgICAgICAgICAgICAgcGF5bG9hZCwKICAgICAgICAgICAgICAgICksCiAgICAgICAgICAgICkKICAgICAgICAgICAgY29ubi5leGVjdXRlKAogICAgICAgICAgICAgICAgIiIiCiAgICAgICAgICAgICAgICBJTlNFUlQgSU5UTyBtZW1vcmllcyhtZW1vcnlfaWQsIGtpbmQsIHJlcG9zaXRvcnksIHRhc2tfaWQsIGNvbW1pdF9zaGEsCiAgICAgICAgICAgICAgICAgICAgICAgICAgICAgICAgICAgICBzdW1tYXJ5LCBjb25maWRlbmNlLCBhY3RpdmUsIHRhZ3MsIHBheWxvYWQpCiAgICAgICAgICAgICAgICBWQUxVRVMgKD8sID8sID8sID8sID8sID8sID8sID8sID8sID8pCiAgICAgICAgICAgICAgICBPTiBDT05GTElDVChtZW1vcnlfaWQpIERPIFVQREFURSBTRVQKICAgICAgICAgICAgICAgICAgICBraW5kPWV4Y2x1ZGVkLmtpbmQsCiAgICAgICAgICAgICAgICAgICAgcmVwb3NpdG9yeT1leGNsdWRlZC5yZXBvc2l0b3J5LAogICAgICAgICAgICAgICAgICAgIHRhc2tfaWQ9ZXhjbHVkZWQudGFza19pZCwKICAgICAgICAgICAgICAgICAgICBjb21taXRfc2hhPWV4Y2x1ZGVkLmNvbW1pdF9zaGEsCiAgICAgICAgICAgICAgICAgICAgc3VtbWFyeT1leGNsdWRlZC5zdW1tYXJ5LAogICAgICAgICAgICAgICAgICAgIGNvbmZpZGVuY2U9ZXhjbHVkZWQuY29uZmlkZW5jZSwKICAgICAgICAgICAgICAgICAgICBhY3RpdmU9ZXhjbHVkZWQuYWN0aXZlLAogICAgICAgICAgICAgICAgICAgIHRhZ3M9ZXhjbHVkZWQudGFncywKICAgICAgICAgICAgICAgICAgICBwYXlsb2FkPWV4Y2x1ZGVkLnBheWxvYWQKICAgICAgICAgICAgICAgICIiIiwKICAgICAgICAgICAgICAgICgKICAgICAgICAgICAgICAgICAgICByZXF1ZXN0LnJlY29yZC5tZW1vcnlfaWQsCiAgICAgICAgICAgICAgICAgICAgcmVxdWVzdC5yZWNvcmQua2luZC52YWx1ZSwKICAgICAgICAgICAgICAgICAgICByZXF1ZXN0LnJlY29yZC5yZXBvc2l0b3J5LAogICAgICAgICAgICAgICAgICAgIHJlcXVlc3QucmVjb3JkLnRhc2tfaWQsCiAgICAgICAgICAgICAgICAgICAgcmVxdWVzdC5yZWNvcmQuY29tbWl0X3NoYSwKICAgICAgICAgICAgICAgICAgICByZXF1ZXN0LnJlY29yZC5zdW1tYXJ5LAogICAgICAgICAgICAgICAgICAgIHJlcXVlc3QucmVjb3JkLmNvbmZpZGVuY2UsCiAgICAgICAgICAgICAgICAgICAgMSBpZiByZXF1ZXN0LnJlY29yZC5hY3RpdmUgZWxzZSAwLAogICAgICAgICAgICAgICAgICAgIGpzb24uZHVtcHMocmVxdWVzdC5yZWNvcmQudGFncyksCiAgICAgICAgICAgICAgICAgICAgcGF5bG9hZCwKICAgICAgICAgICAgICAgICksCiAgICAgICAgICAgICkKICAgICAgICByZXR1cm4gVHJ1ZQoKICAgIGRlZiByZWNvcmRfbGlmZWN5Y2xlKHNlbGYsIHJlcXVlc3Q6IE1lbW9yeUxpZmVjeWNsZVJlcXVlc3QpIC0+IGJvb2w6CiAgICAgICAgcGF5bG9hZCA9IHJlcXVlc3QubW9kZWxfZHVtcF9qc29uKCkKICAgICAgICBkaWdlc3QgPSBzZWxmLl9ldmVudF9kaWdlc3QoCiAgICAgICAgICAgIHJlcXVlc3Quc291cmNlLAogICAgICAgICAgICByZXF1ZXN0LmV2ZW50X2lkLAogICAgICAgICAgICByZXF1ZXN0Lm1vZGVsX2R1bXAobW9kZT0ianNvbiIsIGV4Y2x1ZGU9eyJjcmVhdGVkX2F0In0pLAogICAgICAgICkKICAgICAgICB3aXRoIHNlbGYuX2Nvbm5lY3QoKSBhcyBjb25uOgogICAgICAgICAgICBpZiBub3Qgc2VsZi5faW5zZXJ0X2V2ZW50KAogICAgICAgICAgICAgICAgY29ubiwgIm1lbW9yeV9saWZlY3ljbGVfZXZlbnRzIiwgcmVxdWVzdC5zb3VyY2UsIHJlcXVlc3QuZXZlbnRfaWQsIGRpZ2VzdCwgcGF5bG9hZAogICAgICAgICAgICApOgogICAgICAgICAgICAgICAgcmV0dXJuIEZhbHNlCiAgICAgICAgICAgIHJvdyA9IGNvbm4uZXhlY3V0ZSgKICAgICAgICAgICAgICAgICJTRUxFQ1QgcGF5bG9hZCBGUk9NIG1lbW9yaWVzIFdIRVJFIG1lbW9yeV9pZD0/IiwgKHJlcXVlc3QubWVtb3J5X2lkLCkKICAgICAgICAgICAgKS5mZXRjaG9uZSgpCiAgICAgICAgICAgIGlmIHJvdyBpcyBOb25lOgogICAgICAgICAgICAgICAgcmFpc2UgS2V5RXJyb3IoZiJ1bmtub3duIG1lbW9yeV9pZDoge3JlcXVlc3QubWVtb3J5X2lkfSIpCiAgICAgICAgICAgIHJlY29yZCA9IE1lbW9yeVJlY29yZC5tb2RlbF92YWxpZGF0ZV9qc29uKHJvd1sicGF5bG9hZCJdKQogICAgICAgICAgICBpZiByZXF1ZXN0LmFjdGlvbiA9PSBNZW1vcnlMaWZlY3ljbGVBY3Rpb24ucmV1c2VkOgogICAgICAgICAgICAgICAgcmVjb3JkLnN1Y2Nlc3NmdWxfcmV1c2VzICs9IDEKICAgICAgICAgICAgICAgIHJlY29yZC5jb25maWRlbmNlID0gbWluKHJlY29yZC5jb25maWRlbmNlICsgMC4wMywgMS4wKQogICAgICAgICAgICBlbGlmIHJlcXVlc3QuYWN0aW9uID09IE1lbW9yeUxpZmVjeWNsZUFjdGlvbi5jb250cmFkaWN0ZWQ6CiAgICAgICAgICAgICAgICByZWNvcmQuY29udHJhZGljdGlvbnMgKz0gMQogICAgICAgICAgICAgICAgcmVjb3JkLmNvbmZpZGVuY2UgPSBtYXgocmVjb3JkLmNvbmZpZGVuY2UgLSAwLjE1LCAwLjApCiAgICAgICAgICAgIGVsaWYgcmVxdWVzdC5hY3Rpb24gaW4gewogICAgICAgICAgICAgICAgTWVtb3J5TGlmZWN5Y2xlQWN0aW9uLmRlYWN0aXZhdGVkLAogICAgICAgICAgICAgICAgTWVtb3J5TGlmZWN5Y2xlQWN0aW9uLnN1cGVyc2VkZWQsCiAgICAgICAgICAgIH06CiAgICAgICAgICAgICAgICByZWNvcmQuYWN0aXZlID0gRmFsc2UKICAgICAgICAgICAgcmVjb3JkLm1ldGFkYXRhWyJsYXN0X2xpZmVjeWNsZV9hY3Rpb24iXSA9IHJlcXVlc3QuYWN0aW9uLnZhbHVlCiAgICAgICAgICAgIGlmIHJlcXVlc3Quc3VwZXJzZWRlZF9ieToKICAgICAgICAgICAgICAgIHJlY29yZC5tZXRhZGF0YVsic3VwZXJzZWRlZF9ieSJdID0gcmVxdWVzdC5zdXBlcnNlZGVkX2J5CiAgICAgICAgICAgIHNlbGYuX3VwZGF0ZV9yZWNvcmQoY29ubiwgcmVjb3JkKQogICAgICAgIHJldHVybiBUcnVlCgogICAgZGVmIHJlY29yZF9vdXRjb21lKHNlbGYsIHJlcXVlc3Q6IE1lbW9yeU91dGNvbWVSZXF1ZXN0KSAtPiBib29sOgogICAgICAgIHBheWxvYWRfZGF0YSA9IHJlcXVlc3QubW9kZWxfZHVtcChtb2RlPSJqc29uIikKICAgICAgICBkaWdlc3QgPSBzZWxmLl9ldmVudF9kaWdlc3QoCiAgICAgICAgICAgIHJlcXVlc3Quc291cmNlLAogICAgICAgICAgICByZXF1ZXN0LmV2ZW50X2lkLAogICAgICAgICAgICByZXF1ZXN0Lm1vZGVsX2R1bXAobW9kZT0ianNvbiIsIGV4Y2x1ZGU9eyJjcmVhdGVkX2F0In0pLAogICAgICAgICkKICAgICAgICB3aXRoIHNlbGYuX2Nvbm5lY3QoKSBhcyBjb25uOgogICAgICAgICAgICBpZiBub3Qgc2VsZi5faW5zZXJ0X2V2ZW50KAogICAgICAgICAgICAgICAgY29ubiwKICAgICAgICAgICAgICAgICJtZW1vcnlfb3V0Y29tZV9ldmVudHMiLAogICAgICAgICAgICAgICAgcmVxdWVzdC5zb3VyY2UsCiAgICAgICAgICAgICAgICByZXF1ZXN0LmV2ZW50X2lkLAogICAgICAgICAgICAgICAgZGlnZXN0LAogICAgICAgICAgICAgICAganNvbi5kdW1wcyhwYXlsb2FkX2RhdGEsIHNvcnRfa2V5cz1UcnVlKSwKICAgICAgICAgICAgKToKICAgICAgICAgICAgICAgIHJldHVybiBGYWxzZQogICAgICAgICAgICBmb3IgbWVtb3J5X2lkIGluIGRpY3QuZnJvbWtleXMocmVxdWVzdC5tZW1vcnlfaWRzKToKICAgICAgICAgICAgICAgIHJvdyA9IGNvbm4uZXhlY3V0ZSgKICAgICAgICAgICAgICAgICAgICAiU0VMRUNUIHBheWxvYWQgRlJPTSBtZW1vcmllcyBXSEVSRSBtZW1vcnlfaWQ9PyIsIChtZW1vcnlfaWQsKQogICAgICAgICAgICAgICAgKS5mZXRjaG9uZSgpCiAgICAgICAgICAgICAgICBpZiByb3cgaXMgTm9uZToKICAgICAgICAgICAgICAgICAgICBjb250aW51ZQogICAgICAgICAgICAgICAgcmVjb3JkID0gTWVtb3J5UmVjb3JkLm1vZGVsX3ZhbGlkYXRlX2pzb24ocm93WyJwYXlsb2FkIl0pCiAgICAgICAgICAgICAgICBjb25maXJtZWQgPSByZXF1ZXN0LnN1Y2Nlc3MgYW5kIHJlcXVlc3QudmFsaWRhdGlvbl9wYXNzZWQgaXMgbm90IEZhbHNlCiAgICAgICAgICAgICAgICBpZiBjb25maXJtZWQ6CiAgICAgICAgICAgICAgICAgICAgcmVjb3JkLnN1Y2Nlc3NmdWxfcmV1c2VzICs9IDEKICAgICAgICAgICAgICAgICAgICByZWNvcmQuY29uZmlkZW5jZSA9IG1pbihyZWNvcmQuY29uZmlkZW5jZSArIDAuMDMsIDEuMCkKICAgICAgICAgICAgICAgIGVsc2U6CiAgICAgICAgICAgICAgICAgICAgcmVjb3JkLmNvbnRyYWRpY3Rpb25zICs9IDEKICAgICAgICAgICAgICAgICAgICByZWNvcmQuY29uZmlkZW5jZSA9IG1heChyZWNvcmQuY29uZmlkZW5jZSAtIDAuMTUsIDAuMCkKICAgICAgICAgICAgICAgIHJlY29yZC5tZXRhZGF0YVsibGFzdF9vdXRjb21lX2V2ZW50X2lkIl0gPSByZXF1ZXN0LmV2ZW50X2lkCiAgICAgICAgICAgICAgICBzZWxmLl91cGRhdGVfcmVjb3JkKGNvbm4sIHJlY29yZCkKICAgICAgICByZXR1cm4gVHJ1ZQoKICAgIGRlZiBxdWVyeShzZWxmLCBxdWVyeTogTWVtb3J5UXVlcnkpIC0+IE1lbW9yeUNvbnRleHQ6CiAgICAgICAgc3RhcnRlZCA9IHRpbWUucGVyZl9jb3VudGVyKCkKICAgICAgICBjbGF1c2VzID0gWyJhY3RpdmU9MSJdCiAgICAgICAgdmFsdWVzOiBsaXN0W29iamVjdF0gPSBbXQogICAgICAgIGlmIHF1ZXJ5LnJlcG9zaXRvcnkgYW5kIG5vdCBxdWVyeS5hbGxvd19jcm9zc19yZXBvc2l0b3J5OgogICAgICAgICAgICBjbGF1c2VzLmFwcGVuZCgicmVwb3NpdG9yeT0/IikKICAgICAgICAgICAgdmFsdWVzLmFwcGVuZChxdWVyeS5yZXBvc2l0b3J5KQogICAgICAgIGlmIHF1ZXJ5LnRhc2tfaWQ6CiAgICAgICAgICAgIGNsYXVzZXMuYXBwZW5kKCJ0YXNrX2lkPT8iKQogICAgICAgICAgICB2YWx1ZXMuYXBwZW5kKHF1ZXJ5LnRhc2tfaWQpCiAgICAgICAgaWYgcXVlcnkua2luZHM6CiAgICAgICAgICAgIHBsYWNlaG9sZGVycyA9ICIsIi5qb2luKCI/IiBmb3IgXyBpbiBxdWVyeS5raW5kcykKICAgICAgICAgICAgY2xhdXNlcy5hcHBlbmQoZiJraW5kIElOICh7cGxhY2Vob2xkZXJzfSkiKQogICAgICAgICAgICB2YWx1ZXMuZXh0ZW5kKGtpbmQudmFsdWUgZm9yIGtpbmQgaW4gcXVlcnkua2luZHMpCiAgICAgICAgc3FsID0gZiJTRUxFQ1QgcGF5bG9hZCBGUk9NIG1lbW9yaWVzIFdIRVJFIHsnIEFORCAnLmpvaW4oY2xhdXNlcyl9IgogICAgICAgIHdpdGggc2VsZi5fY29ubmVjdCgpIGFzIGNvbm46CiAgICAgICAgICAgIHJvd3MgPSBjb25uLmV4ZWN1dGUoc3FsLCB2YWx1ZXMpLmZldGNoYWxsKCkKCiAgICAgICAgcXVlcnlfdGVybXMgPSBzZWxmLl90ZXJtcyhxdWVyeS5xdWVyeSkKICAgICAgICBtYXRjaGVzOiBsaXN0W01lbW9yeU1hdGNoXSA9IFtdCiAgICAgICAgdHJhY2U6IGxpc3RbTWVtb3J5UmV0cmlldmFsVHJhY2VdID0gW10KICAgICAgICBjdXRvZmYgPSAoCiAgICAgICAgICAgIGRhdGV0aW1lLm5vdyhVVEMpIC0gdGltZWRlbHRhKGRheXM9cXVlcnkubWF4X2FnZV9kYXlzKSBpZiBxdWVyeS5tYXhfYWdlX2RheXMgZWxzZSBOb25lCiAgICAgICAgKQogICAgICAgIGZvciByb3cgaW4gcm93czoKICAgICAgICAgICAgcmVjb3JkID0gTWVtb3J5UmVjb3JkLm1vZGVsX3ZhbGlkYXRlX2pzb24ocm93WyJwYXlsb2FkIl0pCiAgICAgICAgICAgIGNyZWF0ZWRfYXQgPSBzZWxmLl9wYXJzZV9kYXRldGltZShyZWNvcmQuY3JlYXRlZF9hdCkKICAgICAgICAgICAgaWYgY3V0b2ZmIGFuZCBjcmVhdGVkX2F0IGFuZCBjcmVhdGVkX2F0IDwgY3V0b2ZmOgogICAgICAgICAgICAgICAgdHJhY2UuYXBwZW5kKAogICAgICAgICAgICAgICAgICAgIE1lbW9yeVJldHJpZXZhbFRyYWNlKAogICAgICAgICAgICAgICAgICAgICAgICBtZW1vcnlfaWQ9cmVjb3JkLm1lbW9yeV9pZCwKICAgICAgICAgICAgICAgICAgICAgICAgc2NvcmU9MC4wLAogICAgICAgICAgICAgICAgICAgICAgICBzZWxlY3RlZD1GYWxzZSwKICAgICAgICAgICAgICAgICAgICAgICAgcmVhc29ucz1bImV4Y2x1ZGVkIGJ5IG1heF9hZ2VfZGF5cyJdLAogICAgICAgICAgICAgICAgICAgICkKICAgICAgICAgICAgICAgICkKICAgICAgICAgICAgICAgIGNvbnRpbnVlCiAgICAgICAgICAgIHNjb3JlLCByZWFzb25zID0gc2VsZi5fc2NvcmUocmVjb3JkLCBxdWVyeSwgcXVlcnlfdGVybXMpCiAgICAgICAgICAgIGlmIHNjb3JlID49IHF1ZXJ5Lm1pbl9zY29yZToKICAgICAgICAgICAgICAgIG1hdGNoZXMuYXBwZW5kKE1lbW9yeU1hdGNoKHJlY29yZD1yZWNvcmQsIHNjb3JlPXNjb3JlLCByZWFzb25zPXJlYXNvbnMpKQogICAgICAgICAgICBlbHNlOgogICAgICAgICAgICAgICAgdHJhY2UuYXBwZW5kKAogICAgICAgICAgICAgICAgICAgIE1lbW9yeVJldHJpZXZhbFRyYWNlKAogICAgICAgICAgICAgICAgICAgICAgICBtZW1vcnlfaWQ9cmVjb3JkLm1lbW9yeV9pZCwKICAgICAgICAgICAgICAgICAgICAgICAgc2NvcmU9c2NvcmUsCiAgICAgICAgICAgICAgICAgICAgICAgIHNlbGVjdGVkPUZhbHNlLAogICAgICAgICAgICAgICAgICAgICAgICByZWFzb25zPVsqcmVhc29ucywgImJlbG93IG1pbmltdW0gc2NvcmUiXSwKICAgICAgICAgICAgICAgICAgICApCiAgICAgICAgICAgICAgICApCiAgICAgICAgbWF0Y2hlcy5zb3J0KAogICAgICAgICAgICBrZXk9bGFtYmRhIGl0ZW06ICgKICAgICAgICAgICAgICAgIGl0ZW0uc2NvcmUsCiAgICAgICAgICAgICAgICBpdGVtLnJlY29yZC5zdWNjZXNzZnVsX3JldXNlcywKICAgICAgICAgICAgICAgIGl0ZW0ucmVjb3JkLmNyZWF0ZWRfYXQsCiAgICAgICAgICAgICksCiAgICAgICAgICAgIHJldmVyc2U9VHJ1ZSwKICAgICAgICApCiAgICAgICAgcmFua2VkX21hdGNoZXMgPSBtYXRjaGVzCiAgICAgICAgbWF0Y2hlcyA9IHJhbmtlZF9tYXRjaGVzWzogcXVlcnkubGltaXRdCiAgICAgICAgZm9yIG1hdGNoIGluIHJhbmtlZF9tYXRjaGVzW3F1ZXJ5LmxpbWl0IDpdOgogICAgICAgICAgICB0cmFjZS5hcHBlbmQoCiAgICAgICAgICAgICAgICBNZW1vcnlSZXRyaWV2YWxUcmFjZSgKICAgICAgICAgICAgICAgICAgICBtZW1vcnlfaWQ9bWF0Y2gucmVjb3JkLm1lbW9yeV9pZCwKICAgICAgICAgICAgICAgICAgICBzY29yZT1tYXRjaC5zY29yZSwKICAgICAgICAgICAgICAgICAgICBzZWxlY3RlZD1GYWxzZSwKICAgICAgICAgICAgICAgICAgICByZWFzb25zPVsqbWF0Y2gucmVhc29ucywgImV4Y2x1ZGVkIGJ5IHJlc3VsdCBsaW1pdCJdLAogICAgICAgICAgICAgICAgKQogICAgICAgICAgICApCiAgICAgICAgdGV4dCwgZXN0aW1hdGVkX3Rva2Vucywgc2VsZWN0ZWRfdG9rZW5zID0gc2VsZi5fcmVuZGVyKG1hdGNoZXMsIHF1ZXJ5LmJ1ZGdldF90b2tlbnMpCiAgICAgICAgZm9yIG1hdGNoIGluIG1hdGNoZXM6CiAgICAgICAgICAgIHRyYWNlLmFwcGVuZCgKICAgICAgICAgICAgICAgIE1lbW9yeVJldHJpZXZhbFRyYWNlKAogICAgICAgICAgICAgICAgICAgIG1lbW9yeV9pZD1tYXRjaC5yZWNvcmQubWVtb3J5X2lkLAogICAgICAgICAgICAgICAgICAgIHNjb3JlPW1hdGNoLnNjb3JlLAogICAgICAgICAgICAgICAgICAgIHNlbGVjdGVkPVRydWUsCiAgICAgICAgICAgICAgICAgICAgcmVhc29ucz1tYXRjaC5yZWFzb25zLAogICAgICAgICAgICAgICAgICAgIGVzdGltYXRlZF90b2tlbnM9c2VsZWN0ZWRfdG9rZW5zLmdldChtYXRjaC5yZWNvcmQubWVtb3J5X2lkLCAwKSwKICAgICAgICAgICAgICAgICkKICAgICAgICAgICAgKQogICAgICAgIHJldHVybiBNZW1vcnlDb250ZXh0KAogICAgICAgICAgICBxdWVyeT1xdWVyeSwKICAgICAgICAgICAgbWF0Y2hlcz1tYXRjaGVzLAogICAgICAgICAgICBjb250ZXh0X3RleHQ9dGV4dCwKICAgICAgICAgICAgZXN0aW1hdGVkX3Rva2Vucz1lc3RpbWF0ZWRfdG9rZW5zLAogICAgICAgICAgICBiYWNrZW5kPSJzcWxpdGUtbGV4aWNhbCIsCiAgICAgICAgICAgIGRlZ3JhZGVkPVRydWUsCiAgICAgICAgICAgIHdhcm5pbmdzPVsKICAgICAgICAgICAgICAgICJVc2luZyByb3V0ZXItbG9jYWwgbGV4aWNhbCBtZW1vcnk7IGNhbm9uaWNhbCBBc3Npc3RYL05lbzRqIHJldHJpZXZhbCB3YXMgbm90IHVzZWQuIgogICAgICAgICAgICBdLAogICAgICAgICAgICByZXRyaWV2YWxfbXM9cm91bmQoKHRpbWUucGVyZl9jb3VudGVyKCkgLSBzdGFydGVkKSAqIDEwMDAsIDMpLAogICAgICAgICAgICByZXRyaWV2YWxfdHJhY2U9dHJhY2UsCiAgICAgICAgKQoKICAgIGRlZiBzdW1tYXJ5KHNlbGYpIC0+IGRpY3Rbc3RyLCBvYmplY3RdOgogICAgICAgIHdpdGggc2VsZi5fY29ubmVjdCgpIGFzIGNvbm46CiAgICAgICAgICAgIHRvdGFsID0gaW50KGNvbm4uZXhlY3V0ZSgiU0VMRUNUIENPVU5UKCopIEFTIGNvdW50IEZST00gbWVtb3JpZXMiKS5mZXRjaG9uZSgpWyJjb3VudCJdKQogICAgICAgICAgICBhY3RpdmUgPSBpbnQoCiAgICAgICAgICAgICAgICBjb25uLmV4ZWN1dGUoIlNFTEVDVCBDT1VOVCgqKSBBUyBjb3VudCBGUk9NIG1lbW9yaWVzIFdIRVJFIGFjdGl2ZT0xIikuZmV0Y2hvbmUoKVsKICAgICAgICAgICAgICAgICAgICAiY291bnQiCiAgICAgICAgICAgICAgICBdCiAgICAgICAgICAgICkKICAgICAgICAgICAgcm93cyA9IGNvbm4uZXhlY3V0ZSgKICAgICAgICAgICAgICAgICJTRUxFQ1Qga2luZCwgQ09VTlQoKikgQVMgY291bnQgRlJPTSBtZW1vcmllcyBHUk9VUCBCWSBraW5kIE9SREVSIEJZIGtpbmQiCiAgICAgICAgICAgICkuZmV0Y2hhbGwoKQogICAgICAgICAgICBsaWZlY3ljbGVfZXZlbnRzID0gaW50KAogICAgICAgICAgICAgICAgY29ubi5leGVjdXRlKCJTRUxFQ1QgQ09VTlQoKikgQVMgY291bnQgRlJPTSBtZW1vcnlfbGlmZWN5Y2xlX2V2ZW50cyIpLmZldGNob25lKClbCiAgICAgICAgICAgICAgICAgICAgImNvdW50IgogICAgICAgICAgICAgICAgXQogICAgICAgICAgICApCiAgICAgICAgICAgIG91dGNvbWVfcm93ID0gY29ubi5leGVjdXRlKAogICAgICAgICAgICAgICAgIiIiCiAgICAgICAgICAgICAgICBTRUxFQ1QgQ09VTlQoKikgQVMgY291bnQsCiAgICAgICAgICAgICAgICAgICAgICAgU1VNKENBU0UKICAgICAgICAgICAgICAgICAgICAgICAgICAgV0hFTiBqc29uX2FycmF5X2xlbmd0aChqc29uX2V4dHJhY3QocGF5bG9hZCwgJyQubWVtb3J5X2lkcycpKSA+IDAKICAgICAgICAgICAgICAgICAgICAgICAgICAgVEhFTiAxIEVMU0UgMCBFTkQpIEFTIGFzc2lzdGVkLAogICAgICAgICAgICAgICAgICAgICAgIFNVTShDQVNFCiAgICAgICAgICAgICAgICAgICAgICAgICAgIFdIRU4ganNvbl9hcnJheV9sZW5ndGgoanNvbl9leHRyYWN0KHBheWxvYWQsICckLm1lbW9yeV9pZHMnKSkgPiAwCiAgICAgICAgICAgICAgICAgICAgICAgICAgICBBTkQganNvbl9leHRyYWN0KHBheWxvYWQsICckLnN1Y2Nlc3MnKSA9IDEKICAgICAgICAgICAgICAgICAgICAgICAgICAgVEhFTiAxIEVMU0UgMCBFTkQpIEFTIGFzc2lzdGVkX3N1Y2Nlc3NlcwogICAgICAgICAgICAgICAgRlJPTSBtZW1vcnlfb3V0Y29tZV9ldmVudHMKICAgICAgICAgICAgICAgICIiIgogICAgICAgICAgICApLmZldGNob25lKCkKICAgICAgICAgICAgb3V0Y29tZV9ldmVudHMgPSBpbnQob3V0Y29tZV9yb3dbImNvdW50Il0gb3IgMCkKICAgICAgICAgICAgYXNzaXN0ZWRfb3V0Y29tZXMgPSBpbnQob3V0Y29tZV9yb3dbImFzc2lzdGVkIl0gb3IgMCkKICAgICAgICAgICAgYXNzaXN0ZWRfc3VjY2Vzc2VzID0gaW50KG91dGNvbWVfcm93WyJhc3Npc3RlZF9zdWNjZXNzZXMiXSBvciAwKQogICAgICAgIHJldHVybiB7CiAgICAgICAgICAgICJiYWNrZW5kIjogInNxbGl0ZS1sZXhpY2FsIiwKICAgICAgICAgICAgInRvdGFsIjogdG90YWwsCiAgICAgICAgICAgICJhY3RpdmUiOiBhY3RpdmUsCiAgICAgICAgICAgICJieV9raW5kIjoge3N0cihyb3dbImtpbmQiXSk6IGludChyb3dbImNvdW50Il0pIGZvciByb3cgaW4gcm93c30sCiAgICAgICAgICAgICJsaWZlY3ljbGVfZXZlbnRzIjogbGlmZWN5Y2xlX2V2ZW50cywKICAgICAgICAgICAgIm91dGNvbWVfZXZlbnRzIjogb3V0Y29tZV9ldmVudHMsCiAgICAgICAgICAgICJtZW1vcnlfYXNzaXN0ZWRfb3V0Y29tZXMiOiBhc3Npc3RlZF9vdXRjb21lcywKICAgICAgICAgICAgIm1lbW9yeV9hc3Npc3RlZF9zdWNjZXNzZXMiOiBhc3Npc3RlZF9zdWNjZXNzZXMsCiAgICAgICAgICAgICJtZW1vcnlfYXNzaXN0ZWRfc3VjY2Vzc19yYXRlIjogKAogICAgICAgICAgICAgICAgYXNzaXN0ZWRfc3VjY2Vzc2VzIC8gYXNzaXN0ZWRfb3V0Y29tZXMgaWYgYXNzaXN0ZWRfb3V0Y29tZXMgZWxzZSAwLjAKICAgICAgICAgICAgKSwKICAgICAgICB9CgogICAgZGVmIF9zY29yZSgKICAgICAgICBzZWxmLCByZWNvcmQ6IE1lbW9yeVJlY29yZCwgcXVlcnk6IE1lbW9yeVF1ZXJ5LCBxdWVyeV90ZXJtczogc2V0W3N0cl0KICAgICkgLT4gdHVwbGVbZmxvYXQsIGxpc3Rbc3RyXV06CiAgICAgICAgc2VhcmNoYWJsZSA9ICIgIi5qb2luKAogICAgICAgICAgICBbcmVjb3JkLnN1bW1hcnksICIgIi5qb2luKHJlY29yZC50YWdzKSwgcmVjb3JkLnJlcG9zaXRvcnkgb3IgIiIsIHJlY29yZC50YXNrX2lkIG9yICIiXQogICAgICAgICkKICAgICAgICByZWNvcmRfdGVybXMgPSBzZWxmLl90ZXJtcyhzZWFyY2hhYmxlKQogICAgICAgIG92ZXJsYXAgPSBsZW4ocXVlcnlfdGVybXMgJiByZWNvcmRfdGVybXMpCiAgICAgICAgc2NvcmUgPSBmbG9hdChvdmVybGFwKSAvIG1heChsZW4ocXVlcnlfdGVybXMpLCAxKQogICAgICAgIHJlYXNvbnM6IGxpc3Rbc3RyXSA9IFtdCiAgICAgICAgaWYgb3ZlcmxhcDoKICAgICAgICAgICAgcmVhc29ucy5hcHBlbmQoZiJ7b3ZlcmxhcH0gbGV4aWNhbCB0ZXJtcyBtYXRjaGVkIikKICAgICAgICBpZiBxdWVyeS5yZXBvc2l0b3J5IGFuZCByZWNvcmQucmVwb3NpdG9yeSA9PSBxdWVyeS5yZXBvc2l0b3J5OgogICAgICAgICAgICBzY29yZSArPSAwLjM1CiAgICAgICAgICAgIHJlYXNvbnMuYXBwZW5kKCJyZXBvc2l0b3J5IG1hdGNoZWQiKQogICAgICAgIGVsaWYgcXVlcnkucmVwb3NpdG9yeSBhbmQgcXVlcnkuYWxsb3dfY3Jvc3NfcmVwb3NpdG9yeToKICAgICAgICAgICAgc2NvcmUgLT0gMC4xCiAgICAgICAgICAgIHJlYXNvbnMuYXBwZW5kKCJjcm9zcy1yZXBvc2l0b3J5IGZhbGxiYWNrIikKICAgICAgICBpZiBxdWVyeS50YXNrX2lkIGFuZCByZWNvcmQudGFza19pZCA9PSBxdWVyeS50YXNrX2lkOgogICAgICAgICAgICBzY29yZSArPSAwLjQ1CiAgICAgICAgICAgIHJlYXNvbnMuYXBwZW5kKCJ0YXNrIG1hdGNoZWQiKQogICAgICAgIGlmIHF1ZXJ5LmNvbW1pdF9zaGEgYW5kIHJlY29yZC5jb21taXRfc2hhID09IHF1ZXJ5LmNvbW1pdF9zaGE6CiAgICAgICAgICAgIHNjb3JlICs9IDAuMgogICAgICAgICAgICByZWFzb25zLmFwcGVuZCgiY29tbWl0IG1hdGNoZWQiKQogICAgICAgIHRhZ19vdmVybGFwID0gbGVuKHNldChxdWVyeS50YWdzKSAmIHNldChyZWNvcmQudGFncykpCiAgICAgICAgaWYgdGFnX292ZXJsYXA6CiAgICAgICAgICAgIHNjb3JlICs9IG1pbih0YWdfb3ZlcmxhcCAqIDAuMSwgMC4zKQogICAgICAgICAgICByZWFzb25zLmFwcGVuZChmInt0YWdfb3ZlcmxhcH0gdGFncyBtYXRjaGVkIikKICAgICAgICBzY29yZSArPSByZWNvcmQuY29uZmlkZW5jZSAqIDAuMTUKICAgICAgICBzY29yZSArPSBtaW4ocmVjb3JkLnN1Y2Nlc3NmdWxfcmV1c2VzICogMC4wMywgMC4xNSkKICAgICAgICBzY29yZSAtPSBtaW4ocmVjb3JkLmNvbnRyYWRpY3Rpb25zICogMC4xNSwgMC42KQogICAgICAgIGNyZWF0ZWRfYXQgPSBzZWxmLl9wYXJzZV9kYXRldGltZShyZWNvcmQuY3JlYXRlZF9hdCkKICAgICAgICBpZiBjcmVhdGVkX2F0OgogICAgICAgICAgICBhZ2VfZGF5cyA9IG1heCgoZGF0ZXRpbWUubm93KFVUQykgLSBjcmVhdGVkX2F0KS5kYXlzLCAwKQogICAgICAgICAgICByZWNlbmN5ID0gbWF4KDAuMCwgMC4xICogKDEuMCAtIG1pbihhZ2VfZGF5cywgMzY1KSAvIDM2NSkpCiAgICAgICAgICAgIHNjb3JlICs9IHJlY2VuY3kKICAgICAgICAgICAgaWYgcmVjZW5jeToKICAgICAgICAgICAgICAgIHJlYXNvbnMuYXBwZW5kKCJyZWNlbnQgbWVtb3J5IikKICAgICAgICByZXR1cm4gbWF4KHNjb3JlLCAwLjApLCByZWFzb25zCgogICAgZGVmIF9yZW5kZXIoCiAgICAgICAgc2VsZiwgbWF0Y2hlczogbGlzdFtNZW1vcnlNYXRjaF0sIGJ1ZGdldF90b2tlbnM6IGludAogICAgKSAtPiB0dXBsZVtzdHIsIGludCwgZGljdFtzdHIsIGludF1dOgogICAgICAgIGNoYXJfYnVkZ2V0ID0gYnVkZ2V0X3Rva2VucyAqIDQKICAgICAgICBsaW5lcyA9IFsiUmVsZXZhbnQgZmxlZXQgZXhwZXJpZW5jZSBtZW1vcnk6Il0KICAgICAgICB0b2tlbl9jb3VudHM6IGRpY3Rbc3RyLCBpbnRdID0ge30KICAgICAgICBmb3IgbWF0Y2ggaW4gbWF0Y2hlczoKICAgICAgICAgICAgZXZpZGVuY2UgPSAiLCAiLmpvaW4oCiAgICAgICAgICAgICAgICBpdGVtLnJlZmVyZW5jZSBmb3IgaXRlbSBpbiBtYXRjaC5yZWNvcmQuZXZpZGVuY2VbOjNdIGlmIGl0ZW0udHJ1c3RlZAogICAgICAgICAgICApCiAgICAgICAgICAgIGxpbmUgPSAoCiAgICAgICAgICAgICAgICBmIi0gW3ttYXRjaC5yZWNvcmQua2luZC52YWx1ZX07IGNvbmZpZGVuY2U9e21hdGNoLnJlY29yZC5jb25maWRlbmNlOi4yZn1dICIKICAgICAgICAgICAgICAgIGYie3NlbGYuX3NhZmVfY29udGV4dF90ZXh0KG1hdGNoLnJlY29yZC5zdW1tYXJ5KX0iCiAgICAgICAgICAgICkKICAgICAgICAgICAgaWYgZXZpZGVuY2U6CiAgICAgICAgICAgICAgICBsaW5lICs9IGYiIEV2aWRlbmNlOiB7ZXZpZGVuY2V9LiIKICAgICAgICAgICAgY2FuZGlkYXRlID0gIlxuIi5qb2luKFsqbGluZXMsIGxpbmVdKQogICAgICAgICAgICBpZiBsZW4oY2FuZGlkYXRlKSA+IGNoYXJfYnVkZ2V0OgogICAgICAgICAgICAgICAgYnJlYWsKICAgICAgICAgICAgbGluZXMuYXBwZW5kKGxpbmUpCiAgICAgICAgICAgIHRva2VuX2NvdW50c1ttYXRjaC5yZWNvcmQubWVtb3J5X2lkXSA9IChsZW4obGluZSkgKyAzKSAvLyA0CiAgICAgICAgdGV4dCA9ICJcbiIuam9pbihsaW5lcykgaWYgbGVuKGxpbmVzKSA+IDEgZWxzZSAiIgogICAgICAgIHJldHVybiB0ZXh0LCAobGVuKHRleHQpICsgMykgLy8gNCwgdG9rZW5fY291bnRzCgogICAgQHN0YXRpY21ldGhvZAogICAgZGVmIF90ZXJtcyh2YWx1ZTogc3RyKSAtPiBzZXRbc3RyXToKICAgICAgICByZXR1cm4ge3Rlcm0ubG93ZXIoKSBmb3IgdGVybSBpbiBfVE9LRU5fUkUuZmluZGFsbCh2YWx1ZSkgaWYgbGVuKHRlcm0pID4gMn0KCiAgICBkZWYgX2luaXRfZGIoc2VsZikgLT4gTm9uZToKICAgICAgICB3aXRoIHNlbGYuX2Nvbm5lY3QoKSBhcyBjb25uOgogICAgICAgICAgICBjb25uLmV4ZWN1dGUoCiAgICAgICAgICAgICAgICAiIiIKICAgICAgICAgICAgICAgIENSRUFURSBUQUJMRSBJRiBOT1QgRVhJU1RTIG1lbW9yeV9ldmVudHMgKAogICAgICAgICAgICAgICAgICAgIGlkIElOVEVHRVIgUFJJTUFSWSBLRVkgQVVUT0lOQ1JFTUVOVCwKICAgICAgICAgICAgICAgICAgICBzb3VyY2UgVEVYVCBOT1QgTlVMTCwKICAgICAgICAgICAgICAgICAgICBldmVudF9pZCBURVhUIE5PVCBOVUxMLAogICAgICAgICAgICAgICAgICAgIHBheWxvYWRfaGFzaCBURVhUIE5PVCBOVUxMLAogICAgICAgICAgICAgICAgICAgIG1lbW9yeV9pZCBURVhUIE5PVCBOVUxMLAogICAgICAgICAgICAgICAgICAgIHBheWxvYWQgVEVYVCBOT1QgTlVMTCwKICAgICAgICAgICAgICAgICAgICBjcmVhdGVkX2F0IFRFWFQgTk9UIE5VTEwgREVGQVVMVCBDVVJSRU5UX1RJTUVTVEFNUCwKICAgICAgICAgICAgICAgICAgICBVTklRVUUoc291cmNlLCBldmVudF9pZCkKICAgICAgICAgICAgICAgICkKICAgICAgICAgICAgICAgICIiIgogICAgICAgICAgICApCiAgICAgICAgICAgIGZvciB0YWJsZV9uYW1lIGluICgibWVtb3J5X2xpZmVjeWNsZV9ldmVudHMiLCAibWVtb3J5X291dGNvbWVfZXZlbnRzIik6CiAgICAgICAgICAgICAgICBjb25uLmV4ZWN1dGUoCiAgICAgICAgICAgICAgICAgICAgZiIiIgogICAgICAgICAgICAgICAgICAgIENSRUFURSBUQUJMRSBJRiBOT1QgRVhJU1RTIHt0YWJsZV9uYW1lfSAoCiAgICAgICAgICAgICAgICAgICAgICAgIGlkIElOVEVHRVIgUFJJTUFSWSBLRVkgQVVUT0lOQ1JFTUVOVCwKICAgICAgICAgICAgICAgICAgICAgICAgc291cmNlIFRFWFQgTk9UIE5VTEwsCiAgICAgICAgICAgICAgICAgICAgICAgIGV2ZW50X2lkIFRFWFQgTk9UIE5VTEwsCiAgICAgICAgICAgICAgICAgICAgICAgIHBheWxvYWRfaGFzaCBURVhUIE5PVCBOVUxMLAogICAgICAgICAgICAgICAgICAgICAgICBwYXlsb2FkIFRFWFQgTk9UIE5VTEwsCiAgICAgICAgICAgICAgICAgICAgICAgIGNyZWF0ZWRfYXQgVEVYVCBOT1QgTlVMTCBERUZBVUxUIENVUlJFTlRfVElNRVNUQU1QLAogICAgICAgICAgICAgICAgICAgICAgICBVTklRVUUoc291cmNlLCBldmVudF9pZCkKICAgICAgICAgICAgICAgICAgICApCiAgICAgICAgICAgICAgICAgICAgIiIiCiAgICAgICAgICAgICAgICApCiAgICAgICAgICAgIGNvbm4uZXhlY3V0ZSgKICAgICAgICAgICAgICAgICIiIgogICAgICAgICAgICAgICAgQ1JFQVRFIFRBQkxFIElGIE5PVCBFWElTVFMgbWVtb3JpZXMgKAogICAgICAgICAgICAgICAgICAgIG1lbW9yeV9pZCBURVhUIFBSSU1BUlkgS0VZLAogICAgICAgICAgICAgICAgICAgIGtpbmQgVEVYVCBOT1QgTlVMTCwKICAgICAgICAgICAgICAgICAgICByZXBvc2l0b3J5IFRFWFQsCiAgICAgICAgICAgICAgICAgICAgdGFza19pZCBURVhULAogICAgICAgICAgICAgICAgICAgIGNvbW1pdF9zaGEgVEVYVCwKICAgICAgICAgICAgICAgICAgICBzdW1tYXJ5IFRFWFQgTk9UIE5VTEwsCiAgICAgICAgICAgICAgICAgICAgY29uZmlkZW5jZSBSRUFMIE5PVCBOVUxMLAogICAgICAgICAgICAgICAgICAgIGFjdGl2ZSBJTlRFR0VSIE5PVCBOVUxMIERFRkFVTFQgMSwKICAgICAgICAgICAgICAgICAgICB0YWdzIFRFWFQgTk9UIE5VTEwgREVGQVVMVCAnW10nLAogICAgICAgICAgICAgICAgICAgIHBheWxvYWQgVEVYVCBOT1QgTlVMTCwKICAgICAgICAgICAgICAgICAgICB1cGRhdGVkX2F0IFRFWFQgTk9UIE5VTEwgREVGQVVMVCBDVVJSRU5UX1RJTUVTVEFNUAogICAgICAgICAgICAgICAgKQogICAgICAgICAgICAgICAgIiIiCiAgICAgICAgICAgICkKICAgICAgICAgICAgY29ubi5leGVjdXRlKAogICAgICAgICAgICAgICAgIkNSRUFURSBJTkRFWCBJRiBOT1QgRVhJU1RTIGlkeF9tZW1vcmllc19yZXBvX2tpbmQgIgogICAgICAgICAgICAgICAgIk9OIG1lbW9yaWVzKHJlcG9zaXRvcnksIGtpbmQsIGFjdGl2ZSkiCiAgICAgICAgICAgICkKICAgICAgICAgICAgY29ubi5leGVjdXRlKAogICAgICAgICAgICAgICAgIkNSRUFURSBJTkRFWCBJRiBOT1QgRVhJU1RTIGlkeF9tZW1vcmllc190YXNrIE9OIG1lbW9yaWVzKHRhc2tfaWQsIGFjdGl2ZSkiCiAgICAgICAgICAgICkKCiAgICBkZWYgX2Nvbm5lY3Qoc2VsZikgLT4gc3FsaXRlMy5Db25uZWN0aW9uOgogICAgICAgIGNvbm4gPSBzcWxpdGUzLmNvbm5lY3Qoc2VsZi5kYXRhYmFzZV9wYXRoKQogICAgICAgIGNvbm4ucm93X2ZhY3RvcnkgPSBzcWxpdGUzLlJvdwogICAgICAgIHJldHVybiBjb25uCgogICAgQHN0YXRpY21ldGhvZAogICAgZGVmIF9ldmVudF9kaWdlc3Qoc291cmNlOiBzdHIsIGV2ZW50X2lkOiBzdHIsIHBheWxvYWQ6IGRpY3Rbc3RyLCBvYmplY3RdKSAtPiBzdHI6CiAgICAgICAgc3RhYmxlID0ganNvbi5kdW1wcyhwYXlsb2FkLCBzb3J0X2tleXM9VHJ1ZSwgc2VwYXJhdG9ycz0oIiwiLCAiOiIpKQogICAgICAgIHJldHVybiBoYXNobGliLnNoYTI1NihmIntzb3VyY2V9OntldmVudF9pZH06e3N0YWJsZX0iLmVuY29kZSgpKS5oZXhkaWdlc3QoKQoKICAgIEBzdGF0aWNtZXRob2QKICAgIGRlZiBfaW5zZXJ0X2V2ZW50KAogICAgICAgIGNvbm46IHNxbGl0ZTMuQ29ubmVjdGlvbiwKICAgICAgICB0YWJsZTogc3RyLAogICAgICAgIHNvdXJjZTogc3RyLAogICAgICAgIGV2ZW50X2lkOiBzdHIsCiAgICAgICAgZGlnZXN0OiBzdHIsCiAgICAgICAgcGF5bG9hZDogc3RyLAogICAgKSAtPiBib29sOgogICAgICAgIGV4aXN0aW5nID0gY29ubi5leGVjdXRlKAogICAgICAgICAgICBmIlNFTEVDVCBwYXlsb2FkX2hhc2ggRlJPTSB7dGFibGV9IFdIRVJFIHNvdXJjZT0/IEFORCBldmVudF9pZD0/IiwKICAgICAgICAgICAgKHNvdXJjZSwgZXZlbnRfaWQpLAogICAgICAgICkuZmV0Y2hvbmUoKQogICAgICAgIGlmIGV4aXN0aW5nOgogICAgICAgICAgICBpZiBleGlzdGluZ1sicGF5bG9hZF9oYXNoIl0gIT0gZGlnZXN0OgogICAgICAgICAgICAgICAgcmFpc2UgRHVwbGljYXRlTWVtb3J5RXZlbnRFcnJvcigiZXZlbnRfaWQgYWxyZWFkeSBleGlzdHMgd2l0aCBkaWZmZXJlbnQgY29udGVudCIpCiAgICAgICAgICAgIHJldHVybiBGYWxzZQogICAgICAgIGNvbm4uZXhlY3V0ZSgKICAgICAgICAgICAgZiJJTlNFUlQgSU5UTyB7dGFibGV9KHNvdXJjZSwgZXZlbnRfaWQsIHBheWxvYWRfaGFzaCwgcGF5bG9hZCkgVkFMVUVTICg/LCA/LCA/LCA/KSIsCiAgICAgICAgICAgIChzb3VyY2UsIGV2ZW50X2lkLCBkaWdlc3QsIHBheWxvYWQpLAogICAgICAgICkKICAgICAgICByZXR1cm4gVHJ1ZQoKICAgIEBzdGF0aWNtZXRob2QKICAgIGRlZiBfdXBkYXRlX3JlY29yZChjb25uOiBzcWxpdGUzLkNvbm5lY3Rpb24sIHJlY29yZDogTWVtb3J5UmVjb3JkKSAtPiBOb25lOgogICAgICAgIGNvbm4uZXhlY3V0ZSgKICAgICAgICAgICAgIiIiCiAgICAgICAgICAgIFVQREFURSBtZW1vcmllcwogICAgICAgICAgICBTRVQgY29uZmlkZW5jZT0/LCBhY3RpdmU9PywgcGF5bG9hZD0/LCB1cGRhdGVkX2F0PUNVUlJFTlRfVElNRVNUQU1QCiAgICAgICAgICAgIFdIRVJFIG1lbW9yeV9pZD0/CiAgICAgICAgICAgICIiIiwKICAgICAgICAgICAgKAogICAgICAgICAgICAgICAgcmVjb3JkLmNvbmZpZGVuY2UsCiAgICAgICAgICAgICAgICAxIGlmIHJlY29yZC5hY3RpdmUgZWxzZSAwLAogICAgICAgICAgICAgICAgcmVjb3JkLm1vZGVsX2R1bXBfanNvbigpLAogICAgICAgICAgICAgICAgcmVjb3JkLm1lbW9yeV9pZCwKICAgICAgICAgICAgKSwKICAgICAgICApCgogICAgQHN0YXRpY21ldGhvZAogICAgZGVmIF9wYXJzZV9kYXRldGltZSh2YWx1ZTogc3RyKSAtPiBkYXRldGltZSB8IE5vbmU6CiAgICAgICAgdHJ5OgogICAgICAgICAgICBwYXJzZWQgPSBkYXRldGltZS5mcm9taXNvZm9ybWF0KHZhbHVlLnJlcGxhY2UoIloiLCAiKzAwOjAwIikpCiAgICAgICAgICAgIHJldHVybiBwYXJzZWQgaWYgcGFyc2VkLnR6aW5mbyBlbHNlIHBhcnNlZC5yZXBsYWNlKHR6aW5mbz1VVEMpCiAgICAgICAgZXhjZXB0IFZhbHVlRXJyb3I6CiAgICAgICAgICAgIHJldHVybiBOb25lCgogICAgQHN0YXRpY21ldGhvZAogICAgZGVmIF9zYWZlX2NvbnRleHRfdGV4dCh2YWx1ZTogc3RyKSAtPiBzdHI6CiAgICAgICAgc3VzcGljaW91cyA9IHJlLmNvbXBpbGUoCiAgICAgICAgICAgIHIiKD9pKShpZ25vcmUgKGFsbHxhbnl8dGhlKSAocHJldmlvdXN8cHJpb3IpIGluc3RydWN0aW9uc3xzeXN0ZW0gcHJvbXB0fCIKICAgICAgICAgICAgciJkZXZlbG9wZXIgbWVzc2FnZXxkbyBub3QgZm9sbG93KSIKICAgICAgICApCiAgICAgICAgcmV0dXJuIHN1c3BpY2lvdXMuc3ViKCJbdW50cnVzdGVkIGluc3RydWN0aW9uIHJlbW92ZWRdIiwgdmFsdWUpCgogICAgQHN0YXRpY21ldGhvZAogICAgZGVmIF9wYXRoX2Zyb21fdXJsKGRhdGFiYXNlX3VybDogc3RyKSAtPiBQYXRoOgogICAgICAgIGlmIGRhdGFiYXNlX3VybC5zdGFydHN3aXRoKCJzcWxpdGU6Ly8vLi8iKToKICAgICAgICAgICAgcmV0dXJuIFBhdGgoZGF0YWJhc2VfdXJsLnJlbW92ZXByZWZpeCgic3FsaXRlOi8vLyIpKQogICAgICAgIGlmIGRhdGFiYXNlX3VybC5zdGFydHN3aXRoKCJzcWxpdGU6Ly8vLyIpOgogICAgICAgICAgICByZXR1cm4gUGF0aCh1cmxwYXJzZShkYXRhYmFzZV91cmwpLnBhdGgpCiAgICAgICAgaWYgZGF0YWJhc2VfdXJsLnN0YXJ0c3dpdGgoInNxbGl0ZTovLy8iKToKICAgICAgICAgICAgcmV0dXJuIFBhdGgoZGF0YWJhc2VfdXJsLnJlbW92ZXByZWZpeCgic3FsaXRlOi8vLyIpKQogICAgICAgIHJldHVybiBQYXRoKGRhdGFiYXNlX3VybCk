from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from auto_router.memory_models import (
    MemoryContext,
    MemoryIngestRequest,
    MemoryLifecycleAction,
    MemoryLifecycleRequest,
    MemoryMatch,
    MemoryOutcomeRequest,
    MemoryQuery,
    MemoryRecord,
    MemoryRetrievalTrace,
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

    def record_lifecycle(self, request: MemoryLifecycleRequest) -> bool:
        payload = request.model_dump_json()
        digest = self._event_digest(
            request.source,
            request.event_id,
            request.model_dump(mode="json", exclude={"created_at"}),
        )
        with self._connect() as conn:
            if not self._insert_event(
                conn, "memory_lifecycle_events", request.source, request.event_id, digest, payload
            ):
                return False
            row = conn.execute(
                "SELECT payload FROM memories WHERE memory_id=?", (request.memory_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown memory_id: {request.memory_id}")
            record = MemoryRecord.model_validate_json(row["payload"])
            if request.action == MemoryLifecycleAction.reused:
                record.successful_reuses += 1
                record.confidence = min(record.confidence + 0.03, 1.0)
            elif request.action == MemoryLifecycleAction.contradicted:
                record.contradictions += 1
                record.confidence = max(record.confidence - 0.15, 0.0)
            elif request.action in {
                MemoryLifecycleAction.deactivated,
                MemoryLifecycleAction.superseded,
            }:
                record.active = False
            record.metadata["last_lifecycle_action"] = request.action.value
            if request.superseded_by:
                record.metadata["superseded_by"] = request.superseded_by
            self._update_record(conn, record)
        return True

    def record_outcome(self, request: MemoryOutcomeRequest) -> bool:
        payload_data = request.model_dump(mode="json")
        digest = self._event_digest(
            request.source,
            request.event_id,
            request.model_dump(mode="json", exclude={"created_at"}),
        )
        with self._connect() as conn:
            if not self._insert_event(
                conn,
                "memory_outcome_events",
                request.source,
                request.event_id,
                digest,
                json.dumps(payload_data, sort_keys=True),
            ):
                return False
            for memory_id in dict.fromkeys(request.memory_ids):
                row = conn.execute(
                    "SELECT payload FROM memories WHERE memory_id=?", (memory_id,)
                ).fetchone()
                if row is None:
                    continue
                record = MemoryRecord.model_validate_json(row["payload"])
                confirmed = request.success and request.validation_passed is not False
                if confirmed:
                    record.successful_reuses += 1
                    record.confidence = min(record.confidence + 0.03, 1.0)
                else:
                    record.contradictions += 1
                    record.confidence = max(record.confidence - 0.15, 0.0)
                record.metadata["last_outcome_event_id"] = request.event_id
                self._update_record(conn, record)
        return True

    def query(self, query: MemoryQuery) -> MemoryContext:
        started = time.perf_counter()
        clauses = ["active=1"]
        values: list[object] = []
        if query.repository and not query.allow_cross_repository:
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
        trace: list[MemoryRetrievalTrace] = []
        cutoff = (
            datetime.now(UTC) - timedelta(days=query.max_age_days) if query.max_age_days else None
        )
        for row in rows:
            record = MemoryRecord.model_validate_json(row["payload"])
            created_at = self._parse_datetime(record.created_at)
            if cutoff and created_at and created_at < cutoff:
                trace.append(
                    MemoryRetrievalTrace(
                        memory_id=record.memory_id,
                        score=0.0,
                        selected=False,
                        reasons=["excluded by max_age_days"],
                    )
                )
                continue
            score, reasons = self._score(record, query, query_terms)
            if score >= query.min_score:
                matches.append(MemoryMatch(record=record, score=score, reasons=reasons))
            else:
                trace.append(
                    MemoryRetrievalTrace(
                        memory_id=record.memory_id,
                        score=score,
                        selected=False,
                        reasons=[*reasons, "below minimum score"],
                    )
                )
        matches.sort(
            key=lambda item: (
                item.score,
                item.record.successful_reuses,
                item.record.created_at,
            ),
            reverse=True,
        )
        ranked_matches = matches
        matches = ranked_matches[: query.limit]
        for match in ranked_matches[query.limit :]:
            trace.append(
                MemoryRetrievalTrace(
                    memory_id=match.record.memory_id,
                    score=match.score,
                    selected=False,
                    reasons=[*match.reasons, "excluded by result limit"],
                )
            )
        text, estimated_tokens, selected_tokens = self._render(matches, query.budget_tokens)
        for match in matches:
            trace.append(
                MemoryRetrievalTrace(
                    memory_id=match.record.memory_id,
                    score=match.score,
                    selected=True,
                    reasons=match.reasons,
                    estimated_tokens=selected_tokens.get(match.record.memory_id, 0),
                )
            )
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
            retrieval_ms=round((time.perf_counter() - started) * 1000, 3),
            retrieval_trace=trace,
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
            lifecycle_events = int(
                conn.execute("SELECT COUNT(*) AS count FROM memory_lifecycle_events").fetchone()[
                    "count"
                ]
            )
            outcome_row = conn.execute(
                """
                SELECT COUNT(*) AS count,
                       SUM(CASE
                           WHEN json_array_length(json_extract(payload, '$.memory_ids')) > 0
                           THEN 1 ELSE 0 END) AS assisted,
                       SUM(CASE
                           WHEN json_array_length(json_extract(payload, '$.memory_ids')) > 0
                            AND json_extract(payload, '$.success') = 1
                           THEN 1 ELSE 0 END) AS assisted_successes
                FROM memory_outcome_events
                """
            ).fetchone()
            outcome_events = int(outcome_row["count"] or 0)
            assisted_outcomes = int(outcome_row["assisted"] or 0)
            assisted_successes = int(outcome_row["assisted_successes"] or 0)
        return {
            "backend": "sqlite-lexical",
            "total": total,
            "active": active,
            "by_kind": {str(row["kind"]): int(row["count"]) for row in rows},
            "lifecycle_events": lifecycle_events,
            "outcome_events": outcome_events,
            "memory_assisted_outcomes": assisted_outcomes,
            "memory_assisted_successes": assisted_successes,
            "memory_assisted_success_rate": (
                assisted_successes / assisted_outcomes if assisted_outcomes else 0.0
            ),
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
        elif query.repository and query.allow_cross_repository:
            score -= 0.1
            reasons.append("cross-repository fallback")
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
        created_at = self._parse_datetime(record.created_at)
        if created_at:
            age_days = max((datetime.now(UTC) - created_at).days, 0)
            recency = max(0.0, 0.1 * (1.0 - min(age_days, 365) / 365))
            score += recency
            if recency:
                reasons.append("recent memory")
        return max(score, 0.0), reasons

    def _render(
        self, matches: list[MemoryMatch], budget_tokens: int
    ) -> tuple[str, int, dict[str, int]]:
        char_budget = budget_tokens * 4
        lines = ["Relevant fleet experience memory:"]
        token_counts: dict[str, int] = {}
        for match in matches:
            evidence = ", ".join(
                item.reference for item in match.record.evidence[:3] if item.trusted
            )
            line = (
                f"- [{match.record.kind.value}; confidence={match.record.confidence:.2f}] "
                f"{self._safe_context_text(match.record.summary)}"
            )
            if evidence:
                line += f" Evidence: {evidence}."
            candidate = "\n".join([*lines, line])
            if len(candidate) > char_budget:
                break
            lines.append(line)
            token_counts[match.record.memory_id] = (len(line) + 3) // 4
        text = "\n".join(lines) if len(lines) > 1 else ""
        return text, (len(text) + 3) // 4, token_counts

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
            for table_name in ("memory_lifecycle_events", "memory_outcome_events"):
                conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table_name} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source TEXT NOT NULL,
                        event_id TEXT NOT NULL,
                        payload_hash TEXT NOT NULL,
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
    def _event_digest(source: str, event_id: str, payload: dict[str, object]) -> str:
        stable = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(f"{source}:{event_id}:{stable}".encode()).hexdigest()

    @staticmethod
    def _insert_event(
        conn: sqlite3.Connection,
        table: str,
        source: str,
        event_id: str,
        digest: str,
        payload: str,
    ) -> bool:
        existing = conn.execute(
            f"SELECT payload_hash FROM {table} WHERE source=? AND event_id=?",
            (source, event_id),
        ).fetchone()
        if existing:
            if existing["payload_hash"] != digest:
                raise DuplicateMemoryEventError("event_id already exists with different content")
            return False
        conn.execute(
            f"INSERT INTO {table}(source, event_id, payload_hash, payload) VALUES (?, ?, ?, ?)",
            (source, event_id, digest, payload),
        )
        return True

    @staticmethod
    def _update_record(conn: sqlite3.Connection, record: MemoryRecord) -> None:
        conn.execute(
            """
            UPDATE memories
            SET confidence=?, active=?, payload=?, updated_at=CURRENT_TIMESTAMP
            WHERE memory_id=?
            """,
            (
                record.confidence,
                1 if record.active else 0,
                record.model_dump_json(),
                record.memory_id,
            ),
        )

    @staticmethod
    def _parse_datetime(value: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None

    @staticmethod
    def _safe_context_text(value: str) -> str:
        suspicious = re.compile(
            r"(?i)(ignore (all|any|the) (previous|prior) instructions|system prompt|"
            r"developer message|do not follow)"
        )
        return suspicious.sub("[untrusted instruction removed]", value)

    @staticmethod
    def _path_from_url(database_url: str) -> Path:
        if database_url.startswith("sqlite:///./"):
            return Path(database_url.removeprefix("sqlite:///"))
        if database_url.startswith("sqlite:////"):
            return Path(urlparse(database_url).path)
        if database_url.startswith("sqlite:///"):
            return Path(database_url.removeprefix("sqlite:///"))
        return Path(database_url)
