from __future__ import annotations

import pytest

from auto_router.memory_models import (
    MemoryEvidence,
    MemoryIngestRequest,
    MemoryKind,
    MemoryQuery,
    MemoryRecord,
)
from auto_router.memory_store import DuplicateMemoryEventError, MemoryStore


def _request(summary: str = "Keep AssistX as canonical task authority") -> MemoryIngestRequest:
    return MemoryIngestRequest(
        event_id="event-1",
        source="codex",
        record=MemoryRecord(
            memory_id="lesson-1",
            kind=MemoryKind.lesson,
            summary=summary,
            repository="scottjoyner/auto-router",
            confidence=0.9,
            successful_reuses=2,
            tags=["assistx", "ownership"],
            evidence=[
                MemoryEvidence(
                    evidence_type="repository_document",
                    reference="docs/ROUTER_ASSISTX_AUTO_ASSIGN_BOUNDARIES.md",
                )
            ],
        ),
    )


def test_ingestion_is_idempotent_and_query_is_evidence_backed(tmp_path) -> None:
    store = MemoryStore(f"sqlite:///{tmp_path / 'memory.sqlite3'}")

    assert store.ingest(_request()) is True
    assert store.ingest(_request()) is False

    context = store.query(
        MemoryQuery(
            query="AssistX canonical ownership",
            repository="scottjoyner/auto-router",
            budget_tokens=256,
        )
    )

    assert len(context.matches) == 1
    assert context.matches[0].record.memory_id == "lesson-1"
    assert "ROUTER_ASSISTX_AUTO_ASSIGN_BOUNDARIES.md" in context.context_text
    assert context.backend == "sqlite-lexical"
    assert context.degraded is True


def test_reusing_event_id_with_different_payload_is_rejected(tmp_path) -> None:
    store = MemoryStore(f"sqlite:///{tmp_path / 'memory.sqlite3'}")
    store.ingest(_request())

    with pytest.raises(DuplicateMemoryEventError):
        store.ingest(_request("Conflicting content"))


def test_repository_filter_prevents_cross_repo_leakage(tmp_path) -> None:
    store = MemoryStore(f"sqlite:///{tmp_path / 'memory.sqlite3'}")
    store.ingest(_request())

    context = store.query(
        MemoryQuery(query="AssistX ownership", repository="scottjoyner/auto-insurance")
    )

    assert context.matches == []
    assert context.context_text == ""
