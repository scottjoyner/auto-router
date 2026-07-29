from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MemoryKind(StrEnum):
    observation = "observation"
    failure = "failure"
    resolution = "resolution"
    lesson = "lesson"


class MemoryEvidence(BaseModel):
    evidence_type: str
    reference: str
    commit_sha: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryRecord(BaseModel):
    memory_id: str
    kind: MemoryKind
    summary: str
    repository: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    commit_sha: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    successful_reuses: int = Field(default=0, ge=0)
    contradictions: int = Field(default=0, ge=0)
    active: bool = True
    tags: list[str] = Field(default_factory=list)
    evidence: list[MemoryEvidence] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryIngestRequest(BaseModel):
    event_id: str
    source: str
    record: MemoryRecord


class MemoryQuery(BaseModel):
    query: str
    repository: str | None = None
    task_id: str | None = None
    commit_sha: str | None = None
    kinds: list[MemoryKind] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    limit: int = Field(default=8, ge=1, le=50)
    budget_tokens: int = Field(default=3000, ge=128, le=32000)
    privacy_class: str = "local_only"


class MemoryMatch(BaseModel):
    record: MemoryRecord
    score: float
    reasons: list[str] = Field(default_factory=list)


class MemoryContext(BaseModel):
    query: MemoryQuery
    matches: list[MemoryMatch] = Field(default_factory=list)
    context_text: str = ""
    estimated_tokens: int = 0
    backend: str = "local"
    degraded: bool = False
    warnings: list[str] = Field(default_factory=list)
