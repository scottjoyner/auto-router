from __future__ import annotations

import asyncio
from types import SimpleNamespace

from auto_router import fleet_task_dispatcher_service as service


class _CapturedWriteback:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, task_id: str, **kwargs):
        payload = {"task_id": task_id, **kwargs}
        self.calls.append(payload)
        return payload


def test_task_from_source_preserves_parent_task_id() -> None:
    envelope = service._task_from_source(
        {
            "source": "neo4j",
            "task_id": "task-1",
            "title": "Draft",
            "prompt": "Do the thing",
            "context_note": "Use transcript excerpt",
            "parent_task_id": "root-task-1",
            "task_kind": "research",
            "requires_tools": True,
            "evidence_required": True,
            "capability_lane": "tool_required",
            "evidence_bundle": {"refs": ["task-1"]},
        },
        stage="review",
    )

    assert envelope.stage == "review"
    assert envelope.parent_task_id == "root-task-1"
    assert envelope.context_note == "Use transcript excerpt"
    assert envelope.task_kind == "research"
    assert envelope.requires_tools is True
    assert envelope.evidence_required is True
    assert envelope.capability_lane == "tool_required"
    assert envelope.evidence_bundle == {"refs": ["task-1"]}


def test_task_from_source_promotes_handoff_workflow_stage() -> None:
    envelope = service._task_from_source(
        {
            "source": "neo4j",
            "task_id": "task-handoff",
            "title": "Finalize",
            "prompt": "Finish the review",
            "workflow_stage": "handoff",
        },
        stage="work",
    )

    assert envelope.stage == "handoff"
    assert envelope.workflow_stage == "handoff"


def test_writeback_uses_parent_task_id_for_review_completion(monkeypatch) -> None:
    captured = _CapturedWriteback()
    monkeypatch.setattr(service, "complete_task_in_neo4j", captured)

    task = service.TaskEnvelope(
        stage="review",
        source="auto-review",
        task_id="review-task-1",
        title="Review: task-1",
        prompt="review prompt",
        parent_task_id="task-1",
        task_kind="review",
        evidence_required=True,
        capability_lane="review",
        evidence_bundle={"summary": "draft evidence"},
        enqueued_ms=100,
        claimed_ms=150,
    )
    slot = service.ModelSlot(
        key="xwing/ornith",
        node_name="xwing",
        node_ip="100.108.99.47",
        model="ornith-1.0-35b",
        role="reviewer",
        node_obj=SimpleNamespace(),
    )
    result = SimpleNamespace(
        input_tokens=50,
        output_tokens=100,
        latency_ms=12.5,
        quality_score=0.88,
        response_text="# Final document\n\nDone.",
        response_path="/vault/tasks/task-1.md",
        error=None,
    )

    service._writeback_task_result(task, result, slot, stage="review", success=True, response_path=result.response_path)

    assert len(captured.calls) == 1
    payload = captured.calls[0]
    assert payload["task_id"] == "task-1"
    assert payload["status"] == "DONE"
    assert payload["completed_by"] == "xwing"
    assert payload["completed_model"] == "ornith-1.0-35b"
    assert payload["response_path"] == "/vault/tasks/task-1.md"
    assert payload["final_response_path"] == "/vault/tasks/task-1.md"
    assert payload["draft_response_path"] is None
    assert payload["claimed_by"] is None
    assert payload["task_kind"] == "review"
    assert payload["evidence_required"] is True
    assert payload["capability_lane"] == "review"
    assert payload["outcome_state"] == "accepted"
    assert payload["queue_wait_ms"] == 50.0


def test_writeback_marks_handoff_stage_as_done(monkeypatch) -> None:
    captured = _CapturedWriteback()
    monkeypatch.setattr(service, "complete_task_in_neo4j", captured)

    task = service.TaskEnvelope(
        stage="handoff",
        workflow_stage="handoff",
        source="neo4j",
        task_id="task-3",
        title="Task 3",
        prompt="prompt",
        task_kind="review",
        evidence_required=True,
        capability_lane="handoff",
        enqueued_ms=100,
        claimed_ms=145,
    )
    slot = service.ModelSlot(
        key="xwing/final",
        node_name="xwing",
        node_ip="100.108.99.47",
        model="ornith-1.0-35b",
        role="reviewer",
        node_obj=SimpleNamespace(),
    )
    result = SimpleNamespace(
        input_tokens=10,
        output_tokens=30,
        latency_ms=6.0,
        quality_score=0.9,
        response_text="handoff complete",
        response_path="/vault/tasks/task-3.md",
        error=None,
    )

    service._writeback_task_result(task, result, slot, stage="handoff", success=True, response_path=result.response_path)

    assert len(captured.calls) == 1
    payload = captured.calls[0]
    assert payload["status"] == "DONE"
    assert payload["final_response_path"] == "/vault/tasks/task-3.md"
    assert payload["draft_response_path"] is None
    assert payload["response_path"] == "/vault/tasks/task-3.md"
    assert payload["claimed_by"] is None
    assert payload["outcome_state"] == "accepted"
    assert payload["queue_wait_ms"] == 45.0


def test_writeback_marks_work_stage_as_in_progress_with_draft_path(monkeypatch) -> None:
    captured = _CapturedWriteback()
    monkeypatch.setattr(service, "complete_task_in_neo4j", captured)

    task = service.TaskEnvelope(
        stage="work",
        source="neo4j",
        task_id="task-2",
        title="Task 2",
        prompt="prompt",
        task_kind="analysis",
        requires_tools=True,
        evidence_required=True,
        capability_lane="tool_required",
        enqueued_ms=100,
        claimed_ms=140,
    )
    slot = service.ModelSlot(
        key="xwing/fast",
        node_name="xwing",
        node_ip="100.108.99.47",
        model="qwen2.5-coder-7b",
        role="worker",
        node_obj=SimpleNamespace(),
    )
    result = SimpleNamespace(
        input_tokens=10,
        output_tokens=20,
        latency_ms=5.0,
        quality_score=0.5,
        response_text="hello world",
        response_path="/vault/tasks/task-2.md",
        error=None,
    )

    service._writeback_task_result(task, result, slot, stage="work", success=True, response_path=result.response_path)

    assert len(captured.calls) == 1
    payload = captured.calls[0]
    assert payload["task_id"] == "task-2"
    assert payload["status"] == "IN_PROGRESS"
    assert payload["draft_response_path"] == "/vault/tasks/task-2.md"
    assert payload["response_path"] is None
    assert payload["final_response_path"] is None
    assert payload["claimed_by"] == "xwing"
    assert payload["task_kind"] == "analysis"
    assert payload["requires_tools"] is True
    assert payload["outcome_state"] == "drafted_needs_tools"
    assert payload["queue_wait_ms"] == 40.0


def test_worker_queue_target_drops_during_review_burndown() -> None:
    assert service._worker_queue_target(active_slots=8, review_queue_depth=0) == max(service.WORKER_QUEUE_TARGET, 16)
    assert service._worker_queue_target(active_slots=8, review_queue_depth=service.REVIEW_BURNDOWN_TRIGGER) == service.REVIEW_BURNDOWN_WORKER_TARGET


def test_claim_next_task_prefers_review_when_burndown_is_active() -> None:
    worker_q: asyncio.Queue[service.TaskEnvelope] = asyncio.Queue()
    review_q: asyncio.Queue[service.TaskEnvelope] = asyncio.Queue()
    worker_q.put_nowait(
        service.TaskEnvelope(
            stage="work",
            source="neo4j",
            task_id="work-1",
            title="Worker item",
            prompt="do work",
        )
    )
    review_q.put_nowait(
        service.TaskEnvelope(
            stage="review",
            source="auto-review",
            task_id="review-1",
            title="Review item",
            prompt="do review",
        )
    )
    slot = service.ModelSlot(
        key="xwing/ornith",
        node_name="xwing",
        node_ip="100.108.99.47",
        model="ornith-1.0-35b",
        role="worker",
        node_obj=SimpleNamespace(),
    )

    original_rr_keys = list(service.REVIEWER_RR_KEYS)
    original_rr_index = service.REVIEWER_RR_INDEX
    service.REVIEWER_RR_KEYS = ["xwing/ornith"]
    service.REVIEWER_RR_INDEX = 0
    try:
        claimed = asyncio.run(service._claim_next_task(slot, worker_q, {"xwing/ornith": review_q}, prefer_review=True))

        assert claimed is not None
        assert claimed.task_id == "review-1"
        assert worker_q.qsize() == 1
        assert review_q.qsize() == 0

        claimed = asyncio.run(service._claim_next_task(slot, worker_q, {"xwing/ornith": review_q}, prefer_review=False))

        assert claimed is not None
        assert claimed.task_id == "work-1"
    finally:
        service.REVIEWER_RR_KEYS = original_rr_keys
        service.REVIEWER_RR_INDEX = original_rr_index
