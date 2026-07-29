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


class MemoryLifecycleAction(StrEnum):
    reused = "reused"
    contradicted = "contradicted"
    deactivated = "deactivated"
    superseded = "superseded"


class MemoryEvidence(BaseModel):
    evidence_type: str
    reference: str
    commit_sha: str | None = None
    trusted: bool = False
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
    privacy_class: str = "local_only"


class MemoryQuery(BaseModel):
    query: str
    repository: str | None = None
    task_id: str | None = None
    commit_sha: str | None = None
    kinds: list[MemoryKind] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    limit: int = Field(default=8, ge=1, le=50)
    budget_tokens: int = Field(default=3000, ge=128, le=32000)
    min_score: float = Field(default=0.15, ge=0.0, le=2.0)
    allow_cross_repository: bool = False
    max_age_days: int | None = Field(default=None, ge=1, le=3650)
    privacy_class: str = "local_only"


class MemoryRetrievalTrace(BaseModel):
    memory_id: str
    score: float
    selected: bool
    reasons: list[str] = Field(default_factory=list)
    estimated_tokens: int = 0


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
    retrieval_ms: float = 0.0
    retrieval_trace: list[MemoryRetrievalTrace] = Field(default_factory=list)


class MemoryLifecycleRequest(BaseModel):
    event_id: str
    source: str
    memory_id: str
    action: MemoryLifecycleAction
    reason: str
    superseded_by: str | None = None
    evidence: list[MemoryEvidence] = Field(default_factory=list)
    privacy_class: str = "local_only"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryOutcomeRequest(BaseModel):
    event_id: str
    source: str
    task_id: str
    success: bool
    repository: str | None = None
    commit_sha: str | None = None
    provider: str | None = None
    model: str | None = None
    node_id: str | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    tokens_per_second: float | None = Field(default=None, ge=0)
    validation_passed: bool | None = None
    error_signature: str | None = None
    retry_path: list[str] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)
    privacy_class: str = "local_only"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)
