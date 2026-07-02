from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from auto_router.event_outbox import EventOutbox, OutboxEvent
from auto_router.models import Priority, ProviderCandidate, QuotaEstimate, RouterRequest


class BacklogTaskCandidate(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    prompt: str = ""
    model: str = "auto/backlog-burn"
    priority: Priority = Priority.background
    queue_class: str = "background"
    local_only: bool = False
    allow_cloud: bool | None = True
    sensitive: bool = False
    max_completion_tokens: int = 700
    metadata: dict[str, Any] = Field(default_factory=dict)


class BacklogDryRunRequest(BaseModel):
    tasks: list[BacklogTaskCandidate] = Field(default_factory=list)
    enqueue_events: bool = True
    preserve_realtime_reserve: bool = True
    max_tasks: int | None = None


@dataclass
class BacklogDecision:
    task_id: str
    title: str
    status: str
    reason: str
    profile: str | None = None
    stage: str | None = None
    provider: str | None = None
    model: str | None = None
    queue_class: str | None = None
    quota_estimate: dict[str, Any] | None = None
    event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status,
            "reason": self.reason,
            "profile": self.profile,
            "stage": self.stage,
            "provider": self.provider,
            "model": self.model,
            "queue_class": self.queue_class,
            "quota_estimate": self.quota_estimate,
            "event_id": self.event_id,
        }


def dry_run_backlog_selection(
    request: BacklogDryRunRequest,
    policy_engine: Any,
    quota: Any,
    outbox: EventOutbox | None = None,
    context: Any | None = None,
    dry_run: bool = True,
) -> list[BacklogDecision]:
    tasks = request.tasks[: request.max_tasks] if request.max_tasks else request.tasks
    decisions: list[BacklogDecision] = []
    for task in tasks:
        decision = _decide_task(task, policy_engine=policy_engine, quota=quota)
        if request.enqueue_events and outbox is not None:
            decision.event_id = enqueue_backlog_decision_event(outbox, decision, context=context, dry_run=dry_run)
        decisions.append(decision)
    return decisions


def enqueue_backlog_decision_event(
    outbox: EventOutbox,
    decision: BacklogDecision,
    context: Any | None = None,
    dry_run: bool = True,
) -> str:
    event_type = "router.backlog_job.selected" if decision.status == "selected" else "router.backlog_job.skipped"
    idempotency_key = (
        f"{event_type}:{decision.task_id}:{decision.profile or 'none'}:"
        f"{decision.stage or 'none'}:{decision.provider or 'none'}:{decision.model or 'none'}:{decision.status}"
    )
    payload = {
        "task_id": decision.task_id,
        "title": decision.title,
        "status": decision.status,
        "reason": decision.reason,
        "profile": decision.profile,
        "stage": decision.stage,
        "provider": decision.provider,
        "model": decision.model,
        "queue_class": decision.queue_class,
        "quota_estimate": decision.quota_estimate,
        "dry_run": dry_run,
        "selected_at": int(time.time()),
        "context_revision": getattr(context, "revision", "unknown"),
        "context_source": getattr(context, "source", "unknown"),
    }
    return outbox.enqueue(
        OutboxEvent(
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=payload,
        )
    )


def backlog_summary(decisions: list[BacklogDecision]) -> dict[str, int]:
    return {
        "total": len(decisions),
        "selected": sum(1 for decision in decisions if decision.status == "selected"),
        "skipped": sum(1 for decision in decisions if decision.status == "skipped"),
    }


def _decide_task(task: BacklogTaskCandidate, policy_engine: Any, quota: Any) -> BacklogDecision:
    if task.sensitive:
        return BacklogDecision(task.task_id, task.title, "skipped", "task marked sensitive", queue_class=task.queue_class)
    if task.local_only or task.allow_cloud is False:
        return BacklogDecision(task.task_id, task.title, "skipped", "task requires local-only execution", queue_class=task.queue_class)
    if task.priority not in {Priority.batch, Priority.background}:
        return BacklogDecision(task.task_id, task.title, "skipped", "backlog dry-run only accepts batch/background tasks", queue_class=task.queue_class)
    if str(task.queue_class).lower() not in {"backlog", "background", "batch"}:
        return BacklogDecision(task.task_id, task.title, "skipped", f"queue_class={task.queue_class} is not backlog-eligible", queue_class=task.queue_class)

    router_request = RouterRequest(
        request_id=str(uuid.uuid4()),
        route="chat_completions",
        model=task.model or "auto/backlog-burn",
        messages=[{"role": "user", "content": _safe_planning_prompt(task)}],
        max_tokens=task.max_completion_tokens,
        metadata={**task.metadata, "profile": task.metadata.get("profile", "backlog_burn"), "task_id": task.task_id},
        priority=task.priority,
        local_only=False,
        allow_cloud=True,
        raw_body={
            "model": task.model or "auto/backlog-burn",
            "messages": [{"role": "user", "content": _safe_planning_prompt(task)}],
            "max_completion_tokens": task.max_completion_tokens,
            "metadata": {"profile": task.metadata.get("profile", "backlog_burn"), "task_id": task.task_id},
        },
    )
    plan = policy_engine.plan(router_request)
    for stage in plan.stages:
        candidate, estimate = _first_available_candidate(stage.candidates, quota=quota, body=router_request.raw_body)
        if candidate is None or estimate is None:
            continue
        return BacklogDecision(
            task_id=task.task_id,
            title=task.title,
            status="selected",
            reason="candidate available without reserving quota",
            profile=plan.profile_name,
            stage=stage.purpose.value,
            provider=candidate.provider.name,
            model=candidate.model.alias,
            quota_estimate=_estimate_to_dict(estimate),
            queue_class=task.queue_class,
        )
    return BacklogDecision(
        task_id=task.task_id,
        title=task.title,
        status="skipped",
        reason="no eligible provider/model with available quota",
        profile=plan.profile_name,
        queue_class=task.queue_class,
    )


def _first_available_candidate(
    candidates: list[ProviderCandidate],
    quota: Any,
    body: dict[str, Any],
) -> tuple[ProviderCandidate | None, QuotaEstimate | None]:
    for candidate in candidates:
        estimate = quota.estimate(candidate.model, body)
        can_reserve = getattr(quota, "can_reserve", None)
        if callable(can_reserve):
            if can_reserve(candidate.provider, candidate.model, estimate):
                return candidate, estimate
        else:
            # Conservative fallback: if the quota manager cannot check without
            # mutating, only select local/unlimited-ish candidates.
            if str(candidate.provider.quota_class) == "local":
                return candidate, estimate
    return None, None


def _estimate_to_dict(estimate: QuotaEstimate) -> dict[str, Any]:
    return {
        "request_units": estimate.request_units,
        "input_tokens": estimate.input_tokens,
        "output_tokens": estimate.output_tokens,
        "total_tokens": estimate.total_tokens,
        "dimensions": estimate.dimensions,
    }


def _safe_planning_prompt(task: BacklogTaskCandidate) -> str:
    text = task.prompt.strip() or task.title
    return text[:4000]
