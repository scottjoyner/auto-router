#!/usr/bin/env python3
"""
fleet_task_dispatcher_service.py

Fleet execution consumer for the LM Studio fleet.

What it does:
  - Probes the LM Studio fleet.
  - Creates one worker slot per loaded model.
  - Treats ornith models on x1-370 and xwing as reviewers/refiners.
  - Keeps fast slots busy by immediately assigning the next task.
  - Routes worker output into a review queue so reviewer models can refine it.
  - Claims Neo4j tasks in batches so work is not duplicated.
  - Writes stats snapshots to a shared data path so the live dashboard can read them.

Important:
  - This service is an execution consumer and must not be treated as the canonical assignment governor.
  - auto-assign owns claim/release/lease semantics.
  - AssistX / Neo4j owns canonical task state.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from auto_router.fleet_task_dispatcher import VAULT_WORKSPACE, dispatch_task_async, probe_all_nodes
from auto_router.task_sourcer import complete_task_in_neo4j, get_batch_tasks

TASK_INTERVAL = 6
HEARTBEAT_SECONDS = 15
WORKER_QUEUE_TARGET = 24
REVIEW_BURNDOWN_TRIGGER = int(os.getenv("LM_FLEET_REVIEW_BURNDOWN_TRIGGER", "1"))
REVIEW_BURNDOWN_WORKER_TARGET = int(os.getenv("LM_FLEET_REVIEW_BURNDOWN_WORKER_TARGET", "0"))
RUNNING = True
STATS_PATH = Path(os.getenv("AUTO_ROUTER_FLEET_DISPATCHER_STATS_PATH", str(Path.cwd() / "data" / "fleet_dispatcher_stats.json")))
REVIEWER_NODE_HINTS = {"x1-370", "xwing"}
REVIEWER_MODEL_HINTS = ("ornith", "orinth")
MIN_RESPONSE_CHARS = 20
HANDOFF_STAGES = {"handoff", "final", "finalized", "review_final"}


@dataclass
class TaskEnvelope:
    stage: str  # "work" or "review"
    source: str
    task_id: str
    title: str
    prompt: str
    context_note: str | None = None
    task_kind: str | None = None
    requires_tools: bool = False
    evidence_required: bool = False
    capability_lane: str = "prompt_only"
    queue_class: str | None = None
    evidence_bundle: dict[str, Any] | None = None
    parent_task_id: str | None = None
    origin_node: str | None = None
    origin_model: str | None = None
    enqueued_ms: int = 0
    claimed_ms: int = 0
    started_ms: int = 0
    finished_ms: int = 0
    outcome_state: str | None = None
    outcome_reason: str | None = None
    workflow_stage: str | None = None


@dataclass
class ModelSlot:
    key: str
    node_name: str
    node_ip: str
    model: str
    role: str  # worker | reviewer
    node_obj: Any
    last_seen_ms: int = 0
    in_flight: bool = False
    completed: int = 0
    success: int = 0
    failure: int = 0


STATS: dict[str, Any] = {
    "started_ms": int(time.time() * 1000),
    "completed": 0,
    "success": 0,
    "failure": 0,
    "by_node": {},
    "by_model": {},
    "by_role": {},
    "by_stage": {},
    "by_source": {},
    "by_task_kind": {},
    "by_lane": {},
    "by_outcome": {},
    "by_failure_reason": {},
    "queue_wait_ms_total": 0.0,
    "queue_wait_count": 0,
    "dispatch_latency_ms_total": 0.0,
    "dispatch_latency_count": 0,
    "latency_ms_total": 0.0,
    "latency_count": 0,
    "quality_total": 0.0,
    "quality_count": 0,
    "response_chars_total": 0,
    "response_chars_count": 0,
    "recent_snapshots": [],
}


ACTIVE_SLOT_KEYS: set[str] = set()
SLOTS: dict[str, ModelSlot] = {}
WORKER_QUEUE: asyncio.Queue[TaskEnvelope] | None = None
REVIEW_QUEUES: dict[str, asyncio.Queue[TaskEnvelope]] = {}
REVIEWER_RR_KEYS: list[str] = []
REVIEWER_RR_INDEX = 0


def _is_reviewer(node_name: str, model: str) -> bool:
    m = model.lower()
    n = node_name.lower()
    return n in REVIEWER_NODE_HINTS or any(h in m for h in REVIEWER_MODEL_HINTS)


def _slot_key(node_name: str, model: str) -> str:
    return f"{node_name}/{model}"


def _slot_role(node_name: str, model: str) -> str:
    return "reviewer" if _is_reviewer(node_name, model) else "worker"


def _worker_queue_target(active_slots: int, review_queue_depth: int) -> int:
    base_target = max(WORKER_QUEUE_TARGET, active_slots * 2)
    if review_queue_depth >= REVIEW_BURNDOWN_TRIGGER:
        return min(base_target, REVIEW_BURNDOWN_WORKER_TARGET)
    return base_target


def _bump_counter(bucket: dict[str, int], key: str | None, amount: int = 1) -> None:
    if not key:
        key = "unknown"
    bucket[key] = bucket.get(key, 0) + amount


def _normalize_stage(stage: str | None, workflow_stage: str | None = None) -> str:
    stage_value = str(stage or "").strip().lower()
    workflow_value = str(workflow_stage or "").strip().lower()
    if stage_value in {"handoff", "final", "finalized", "review_final"}:
        return "handoff"
    if workflow_value in HANDOFF_STAGES:
        return "handoff"
    if stage_value in {"review", "work"}:
        return stage_value
    return stage_value or "work"


def _task_outcome_state(task: TaskEnvelope, success: bool) -> tuple[str, str | None]:
    stage = _normalize_stage(task.stage, task.workflow_stage)
    if not success:
        return "rejected", "model_error_or_short_response"
    if stage in {"review", "handoff"}:
        return "accepted", None
    if task.requires_tools:
        return "drafted_needs_tools", None if task.evidence_required else "prompt_only_draft"
    return "drafted", None


def _record_stats(
    slot: ModelSlot,
    task: TaskEnvelope,
    stage: str,
    success: bool,
    *,
    outcome_state: str | None = None,
    queue_wait_ms: float | None = None,
    dispatch_latency_ms: float | None = None,
    result: Any | None = None,
) -> None:
    STATS["completed"] += 1
    _bump_counter(STATS["by_stage"], stage)
    _bump_counter(STATS["by_role"], slot.role)
    _bump_counter(STATS["by_node"], slot.node_name)
    _bump_counter(STATS["by_model"], slot.model)
    _bump_counter(STATS["by_source"], task.source)
    _bump_counter(STATS["by_task_kind"], task.task_kind)
    _bump_counter(STATS["by_lane"], task.capability_lane)
    _bump_counter(STATS["by_outcome"], outcome_state)
    if success:
        STATS["success"] += 1
    else:
        STATS["failure"] += 1
        reason = (getattr(result, "error", None) or task.outcome_reason or "failed").strip() if hasattr(getattr(result, "error", None), "strip") else (getattr(result, "error", None) or task.outcome_reason or "failed")
        _bump_counter(STATS["by_failure_reason"], str(reason))

    if queue_wait_ms is not None:
        STATS["queue_wait_ms_total"] += float(queue_wait_ms)
        STATS["queue_wait_count"] += 1
    if dispatch_latency_ms is not None:
        STATS["dispatch_latency_ms_total"] += float(dispatch_latency_ms)
        STATS["dispatch_latency_count"] += 1
    if result is not None:
        if getattr(result, "latency_ms", None) is not None:
            STATS["latency_ms_total"] += float(result.latency_ms)
            STATS["latency_count"] += 1
        if getattr(result, "quality_score", None) is not None:
            STATS["quality_total"] += float(result.quality_score)
            STATS["quality_count"] += 1
        if getattr(result, "response_text", None) is not None:
            STATS["response_chars_total"] += len(str(result.response_text))
            STATS["response_chars_count"] += 1


def _writeback_task_result(task: TaskEnvelope, result: Any, slot: ModelSlot, *, stage: str, success: bool, response_path: str | None = None) -> None:
    stage = _normalize_stage(stage, task.workflow_stage)
    task_id = task.parent_task_id or task.task_id
    if not task_id:
        return

    status = "DONE" if success and stage in {"review", "handoff"} else ("FAILED" if not success else "IN_PROGRESS")
    outcome_state, outcome_reason = _task_outcome_state(task, success)
    task.outcome_state = outcome_state
    task.outcome_reason = outcome_reason
    queue_wait_ms = None
    if task.claimed_ms and task.enqueued_ms:
        queue_wait_ms = max(float(task.claimed_ms - task.enqueued_ms), 0.0)
    try:
        complete_task_in_neo4j(
            task_id,
            status=status,
            completed_by=slot.node_name,
            completed_node=slot.node_name,
            completed_model=slot.model,
            stage=stage,
            response_path=response_path if stage != "work" else None,
            draft_response_path=response_path if stage == "work" else None,
            final_response_path=response_path if stage in {"review", "handoff"} and success else None,
            input_tokens=getattr(result, "input_tokens", None) or None,
            output_tokens=getattr(result, "output_tokens", None) or None,
            latency_ms=getattr(result, "latency_ms", None) or None,
            quality_score=getattr(result, "quality_score", None) or None,
            response_chars=len((getattr(result, "response_text", None) or "").strip()) or None,
            error=getattr(result, "error", None),
            claimed_by=slot.node_name if stage == "work" else None,
            completion_source="fleet_task_dispatcher_service",
            task_kind=task.task_kind,
            requires_tools=task.requires_tools,
            evidence_required=task.evidence_required,
            capability_lane=task.capability_lane,
            evidence_bundle=task.evidence_bundle,
            outcome_state=outcome_state,
            outcome_reason=outcome_reason,
            queue_wait_ms=queue_wait_ms,
            dispatch_latency_ms=getattr(result, "latency_ms", None) or None,
        )
    except Exception as exc:
        print(f"[{time.strftime('%H:%M:%S')}] neo4j write-back error for {task_id}: {exc}")


def _review_prompt(task: TaskEnvelope, response_text: str, slot: ModelSlot) -> str:
    draft = response_text.strip()
    if len(draft) > 14_000:
        draft = draft[:14_000]
    context_block = ""
    if task.context_note:
        context_block = f"\n\nSOURCE CONTEXT START\n{task.context_note}\nSOURCE CONTEXT END\n"
    evidence_block = ""
    if task.evidence_bundle:
        evidence_block = f"\n\nEVIDENCE BUNDLE START\n{json.dumps(task.evidence_bundle, indent=2, sort_keys=True)}\nEVIDENCE BUNDLE END\n"
    return (
        "You are the stronger reviewer model. Review, refine, and improve the draft below. "
        "Fix reasoning issues, tighten structure, preserve useful details, and return only a polished markdown document.\n\n"
        f"Original task: {task.title}\n"
        f"Task kind: {task.task_kind or 'general'}\n"
        f"Capability lane: {task.capability_lane}\n"
        f"Requires tools: {task.requires_tools}\n"
        f"Evidence required: {task.evidence_required}\n"
        f"Produced by: {slot.node_name}/{slot.model}\n"
        f"Source stage: {task.stage}\n"
        f"{context_block}\n"
        f"{evidence_block}\n"
        f"DRAFT START\n{draft}\nDRAFT END\n"
    )


def _next_reviewer_key() -> str | None:
    global REVIEWER_RR_INDEX
    if not REVIEWER_RR_KEYS:
        return None
    key = REVIEWER_RR_KEYS[REVIEWER_RR_INDEX % len(REVIEWER_RR_KEYS)]
    REVIEWER_RR_INDEX = (REVIEWER_RR_INDEX + 1) % max(1, len(REVIEWER_RR_KEYS))
    return key


def _enqueue_review_task(task: TaskEnvelope) -> None:
    key = _next_reviewer_key()
    if not key:
        return
    q = REVIEW_QUEUES.setdefault(key, asyncio.Queue())
    q.put_nowait(task)


def _next_review_queue(review_queues: dict[str, asyncio.Queue[TaskEnvelope]]) -> asyncio.Queue[TaskEnvelope] | None:
    key = _next_reviewer_key()
    if not key:
        return None
    return review_queues.get(key)


def _review_queue_candidates(review_queues: dict[str, asyncio.Queue[TaskEnvelope]]) -> list[asyncio.Queue[TaskEnvelope]]:
    if REVIEWER_RR_KEYS:
        return [review_queues[key] for key in REVIEWER_RR_KEYS if key in review_queues]
    return list(review_queues.values())


def _task_from_source(item: dict[str, Any], stage: str = "work") -> TaskEnvelope:
    evidence_bundle = item.get("evidence_bundle")
    if evidence_bundle is not None and not isinstance(evidence_bundle, dict):
        evidence_bundle = {"value": evidence_bundle}
    workflow_stage = str(item.get("workflow_stage") or item.get("stage") or "").strip().lower() or None
    normalized_stage = _normalize_stage(stage, workflow_stage)
    return TaskEnvelope(
        stage=normalized_stage,
        source=str(item.get("source", "neo4j")),
        task_id=str(item.get("task_id") or item.get("id") or item.get("title") or time.time_ns()),
        title=str(item.get("title") or item.get("prompt") or "untitled task"),
        prompt=str(item.get("prompt") or item.get("title") or ""),
        context_note=str(item.get("context_note") or "") or None,
        task_kind=str(item.get("task_kind") or "general"),
        requires_tools=bool(item.get("requires_tools", False)),
        evidence_required=bool(item.get("evidence_required", False)),
        capability_lane=str(item.get("capability_lane") or "prompt_only"),
        queue_class=str(item.get("queue_class") or "") or None,
        evidence_bundle=evidence_bundle,
        parent_task_id=str(item.get("parent_task_id") or item.get("source_task_id") or item.get("task_id") or item.get("id") or "") or None,
        enqueued_ms=int(item.get("enqueued_ms") or time.time() * 1000),
        workflow_stage=workflow_stage,
    )


async def _save_stats(nodes: list[Any], worker_q: asyncio.Queue[TaskEnvelope], review_queues: dict[str, asyncio.Queue[TaskEnvelope]]) -> None:
    review_queue_size = sum(q.qsize() for q in review_queues.values())
    queue_wait_avg = STATS["queue_wait_ms_total"] / STATS["queue_wait_count"] if STATS["queue_wait_count"] else 0.0
    dispatch_latency_avg = STATS["dispatch_latency_ms_total"] / STATS["dispatch_latency_count"] if STATS["dispatch_latency_count"] else 0.0
    latency_avg = STATS["latency_ms_total"] / STATS["latency_count"] if STATS["latency_count"] else 0.0
    quality_avg = STATS["quality_total"] / STATS["quality_count"] if STATS["quality_count"] else 0.0
    response_chars_avg = STATS["response_chars_total"] / STATS["response_chars_count"] if STATS["response_chars_count"] else 0.0
    snapshot = {
        "time_ms": int(time.time() * 1000),
        "queues": {
            "worker": worker_q.qsize(),
            "review": review_queue_size,
        },
        "summary": {
            "online_nodes": sum(1 for n in nodes if n.online),
            "online_nodes_with_loaded_models": sum(1 for n in nodes if n.online and n.loaded_models),
            "active_slots": len(ACTIVE_SLOT_KEYS),
            "busy_slots": sum(1 for s in SLOTS.values() if s.in_flight),
            "idle_slots": max(len(ACTIVE_SLOT_KEYS) - sum(1 for s in SLOTS.values() if s.in_flight), 0),
            "reviewer_slots": sum(1 for s in SLOTS.values() if s.role == "reviewer"),
            "worker_slots": sum(1 for s in SLOTS.values() if s.role == "worker"),
            "completed": STATS["completed"],
            "success": STATS["success"],
            "failure": STATS["failure"],
            "avg_queue_wait_ms": round(queue_wait_avg, 3),
            "avg_dispatch_latency_ms": round(dispatch_latency_avg, 3),
            "avg_latency_ms": round(latency_avg, 3),
            "avg_quality_score": round(quality_avg, 3),
            "avg_response_chars": round(response_chars_avg, 3),
        },
        "stats": {
            "completed": STATS["completed"],
            "success": STATS["success"],
            "failure": STATS["failure"],
            "by_stage": dict(STATS["by_stage"]),
            "by_role": dict(STATS["by_role"]),
            "by_node": dict(STATS["by_node"]),
        },
    }
    recent_snapshots = list(STATS.get("recent_snapshots", []))
    recent_snapshots.append(snapshot)
    recent_snapshots = recent_snapshots[-2:]
    STATS["recent_snapshots"] = recent_snapshots
    payload = {
        **snapshot,
        "stats": {
            **STATS,
            "recent_snapshots": recent_snapshots,
        },
        "slots": [
            {
                "key": s.key,
                "node": s.node_name,
                "model": s.model,
                "role": s.role,
                "in_flight": s.in_flight,
                "completed": s.completed,
                "success": s.success,
                "failure": s.failure,
            }
            for s in sorted(SLOTS.values(), key=lambda x: (x.role, x.node_name, x.model))
        ],
    }
    STATS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True))


async def _claim_next_task(
    slot: ModelSlot,
    worker_q: asyncio.Queue[TaskEnvelope],
    review_queues: dict[str, asyncio.Queue[TaskEnvelope]],
    *,
    prefer_review: bool = False,
) -> TaskEnvelope | None:
    if slot.role == "reviewer":
        preferred = review_queues.get(slot.key)
        fallback = worker_q
        queues = [preferred, fallback] if not prefer_review else [preferred, fallback]
    else:
        # Worker slots should mostly drain the worker queue, but when the review backlog is hot
        # they should help clear review before taking more work items.
        review_qs = _review_queue_candidates(review_queues)
        queues = [*review_qs, worker_q] if prefer_review else [worker_q, *review_qs]

    for q in queues:
        if q is None:
            continue
        try:
            return q.get_nowait()
        except asyncio.QueueEmpty:
            continue
    return None


async def _slot_loop(slot_key: str, worker_q: asyncio.Queue[TaskEnvelope], review_queues: dict[str, asyncio.Queue[TaskEnvelope]]) -> None:
    while RUNNING:
        slot = SLOTS.get(slot_key)
        if slot is None:
            await asyncio.sleep(1)
            continue

        if slot_key not in ACTIVE_SLOT_KEYS:
            break

        prefer_review = sum(q.qsize() for q in review_queues.values()) >= REVIEW_BURNDOWN_TRIGGER
        task = await _claim_next_task(slot, worker_q, review_queues, prefer_review=prefer_review)
        if task is None:
            await asyncio.sleep(0.5)
            continue

        slot.in_flight = True
        slot.last_seen_ms = int(time.time() * 1000)
        STATS["by_stage"][task.stage] = STATS["by_stage"].get(task.stage, 0) + 0  # ensure key exists

        try:
            task.claimed_ms = int(time.time() * 1000)
            task.started_ms = task.claimed_ms
            queue_wait_ms = max(float(task.claimed_ms - task.enqueued_ms), 0.0) if task.enqueued_ms else None
            dispatch_prompt = task.prompt
            if task.context_note:
                dispatch_prompt = f"{task.prompt}\n\nSOURCE CONTEXT START\n{task.context_note}\nSOURCE CONTEXT END"
            if task.evidence_bundle:
                dispatch_prompt += f"\n\nEVIDENCE BUNDLE START\n{json.dumps(task.evidence_bundle, indent=2, sort_keys=True)}\nEVIDENCE BUNDLE END"
            dispatch_prompt += (
                f"\n\nTASK META\nkind={task.task_kind or 'general'}\n"
                f"requires_tools={task.requires_tools}\n"
                f"evidence_required={task.evidence_required}\n"
                f"capability_lane={task.capability_lane}\n"
            )
            result = await dispatch_task_async(slot.node_obj, dispatch_prompt, preferred_model=slot.model)
            task.finished_ms = int(time.time() * 1000)
        except Exception as exc:
            slot.failure += 1
            task.finished_ms = int(time.time() * 1000)
            task.outcome_state, task.outcome_reason = _task_outcome_state(task, False)
            failure_result = SimpleNamespace(error=str(exc), latency_ms=0, output_tokens=0, response_text="", quality_score=0.0, response_path=None)
            _record_stats(slot, task, task.stage, False, outcome_state=task.outcome_state, queue_wait_ms=max(float(task.claimed_ms - task.enqueued_ms), 0.0) if task.claimed_ms and task.enqueued_ms else None, result=failure_result)
            _writeback_task_result(task, failure_result, slot, stage=task.stage, success=False, response_path=None)
            print(f"[{time.strftime('%H:%M:%S')}] {slot.node_name}/{slot.model}: {exc}")
            slot.in_flight = False
            continue

        success = not result.error and bool(result.response_text and result.response_text.strip())
        if success and len((result.response_text or "").strip()) < MIN_RESPONSE_CHARS:
            success = False

        slot.completed += 1
        if success:
            slot.success += 1
        else:
            slot.failure += 1

        task.outcome_state, task.outcome_reason = _task_outcome_state(task, success)
        _record_stats(
            slot,
            task,
            _normalize_stage(task.stage, task.workflow_stage),
            success,
            outcome_state=task.outcome_state,
            queue_wait_ms=max(float(task.claimed_ms - task.enqueued_ms), 0.0) if task.claimed_ms and task.enqueued_ms else None,
            dispatch_latency_ms=getattr(result, "latency_ms", None) or None,
            result=result,
        )
        recorded_stage = _normalize_stage(task.stage, task.workflow_stage)
        _writeback_task_result(task, result, slot, stage=recorded_stage, success=success, response_path=result.response_path)

        status = "OK" if success else f"ERR: {result.error or 'short/empty response'}"
        print(
            f"[{time.strftime('%H:%M:%S')}] {slot.node_name:<30} {slot.model[:28]:<28} "
            f"[{slot.role[:8]}] {recorded_stage:<6} {result.output_tokens:>5} tok  {result.latency_ms:.0f}ms  {status}"
        )

        if success and recorded_stage == "work" and result.response_text:
            review_prompt = _review_prompt(task, result.response_text, slot)
            review_task = TaskEnvelope(
                stage="review",
                source="auto-review",
                task_id=f"{task.task_id}:review:{time.time_ns()}",
                title=f"Review: {task.title}",
                prompt=review_prompt,
                task_kind="review",
                requires_tools=False,
                evidence_required=True,
                capability_lane="review",
                evidence_bundle=task.evidence_bundle,
                parent_task_id=task.parent_task_id or task.task_id,
                origin_node=slot.node_name,
                origin_model=slot.model,
                enqueued_ms=int(time.time() * 1000),
                workflow_stage="handoff",
            )
            _enqueue_review_task(review_task)

        slot.in_flight = False


async def _prefill_worker_queue(worker_q: asyncio.Queue[TaskEnvelope], desired_size: int) -> None:
    # Claim new tasks only when the queue is running low.
    if worker_q.qsize() >= desired_size:
        return

    fetch_count = max(4, min(24, desired_size - worker_q.qsize()))
    batch = await asyncio.to_thread(get_batch_tasks, fetch_count)
    for item in batch:
        await worker_q.put(_task_from_source(item, stage="work"))


async def _manager_loop() -> None:
    global WORKER_QUEUE, REVIEW_QUEUES, REVIEWER_RR_KEYS, REVIEWER_RR_INDEX

    WORKER_QUEUE = asyncio.Queue()
    REVIEW_QUEUES = {}
    REVIEWER_RR_KEYS = []
    REVIEWER_RR_INDEX = 0

    slot_tasks: dict[str, asyncio.Task[Any]] = {}
    feeder_last_run = 0.0

    while RUNNING:
        nodes = await asyncio.to_thread(probe_all_nodes)

        # Rebuild slot inventory from the latest probe.
        new_slots: dict[str, ModelSlot] = {}
        new_active: set[str] = set()
        for node in nodes:
            if not node.online:
                continue
            for model in node.loaded_models:
                key = _slot_key(node.name, model)
                role = _slot_role(node.name, model)
                new_slots[key] = ModelSlot(
                    key=key,
                    node_name=node.name,
                    node_ip=node.ip,
                    model=model,
                    role=role,
                    node_obj=node,
                    last_seen_ms=int(time.time() * 1000),
                )
                new_active.add(key)

        ACTIVE_SLOT_KEYS.clear()
        ACTIVE_SLOT_KEYS.update(new_active)

        # Replace slot objects in place so long-running workers see the latest snapshot.
        for key, slot in new_slots.items():
            SLOTS[key] = slot

        # Remove stale slots from the registry; worker loops exit when they see the key is inactive.
        for key in list(SLOTS.keys()):
            if key not in new_active:
                SLOTS.pop(key, None)

        reviewer_keys = sorted(k for k, s in SLOTS.items() if s.role == "reviewer")
        REVIEWER_RR_KEYS = reviewer_keys
        for key in reviewer_keys:
            REVIEW_QUEUES.setdefault(key, asyncio.Queue())
        for key in list(REVIEW_QUEUES.keys()):
            if key not in reviewer_keys:
                REVIEW_QUEUES.pop(key, None)

        # Start one long-lived worker per slot.
        for key in new_active:
            if key not in slot_tasks or slot_tasks[key].done():
                slot_tasks[key] = asyncio.create_task(_slot_loop(key, WORKER_QUEUE, REVIEW_QUEUES))

        # Keep the worker queue filled so fast slots never have to wait for the next cycle.
        if time.time() - feeder_last_run >= 1.0:
            feeder_last_run = time.time()
            try:
                review_queue_depth = sum(q.qsize() for q in REVIEW_QUEUES.values())
                await _prefill_worker_queue(WORKER_QUEUE, _worker_queue_target(len(new_active), review_queue_depth))
            except Exception as exc:
                print(f"[{time.strftime('%H:%M:%S')}] feeder error: {exc}")

        # Update stats snapshot.
        try:
            await _save_stats(nodes, WORKER_QUEUE, REVIEW_QUEUES)
        except Exception as exc:
            print(f"[{time.strftime('%H:%M:%S')}] stats write error: {exc}")

        online_with_models = sum(1 for n in nodes if n.online and n.loaded_models)
        worker_slots = sum(1 for s in SLOTS.values() if s.role == "worker")
        reviewer_slots = sum(1 for s in SLOTS.values() if s.role == "reviewer")
        print(
            f"[{time.strftime('%H:%M:%S')}] fleet online={sum(1 for n in nodes if n.online)} "
            f"slots={len(new_active)} worker={worker_slots} reviewer={reviewer_slots} "
            f"queues(work={WORKER_QUEUE.qsize()} review={sum(q.qsize() for q in REVIEW_QUEUES.values())}) "
            f"active_nodes={online_with_models} completed={STATS['completed']}"
        )

        await asyncio.sleep(HEARTBEAT_SECONDS)

    # Shutdown.
    for task in slot_tasks.values():
        task.cancel()
    await asyncio.gather(*slot_tasks.values(), return_exceptions=True)


async def _run_stats_only() -> None:
    if STATS_PATH.exists():
        print(STATS_PATH.read_text())
    else:
        print("No stats available yet.")


def signal_handler(signum, frame):
    global RUNNING
    print(f"\nReceived signal {signum}. Stopping...")
    RUNNING = False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Fleet Task Dispatcher Service")
    parser.add_argument("--stats", action="store_true", help="Show dispatcher stats snapshot")
    args = parser.parse_args()

    if args.stats:
        asyncio.run(_run_stats_only())
        return

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    VAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)

    print("Transitional fleet execution consumer started.")
    print("NOTE: auto-assign owns assignment governance; this service is not canonical.")
    print(f"Vault workspace: {VAULT_WORKSPACE}")
    print(f"Heartbeat: {HEARTBEAT_SECONDS}s")
    print(f"Worker queue target: {WORKER_QUEUE_TARGET}")
    print("Reviewer models: ornith on x1-370/xwing when loaded")

    try:
        asyncio.run(_manager_loop())
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
