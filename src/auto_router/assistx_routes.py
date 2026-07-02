from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from auto_router.agent_jobs import build_agent_job_request
from auto_router.event_outbox import EventOutbox, OutboxEvent
from auto_router.models import RouteDecision, RouteRequest
from auto_router.route_events import ensure_event_outbox

router = APIRouter(tags=["assistx-routes"])


def _build_route_decision(
    request: RouteRequest,
    *,
    lane: str,
    provider: str,
    model: str,
    target_service: str | None = None,
    target_node_id: str | None = None,
    target_job_id: str | None = None,
    target_worker: str | None = None,
    rationale: str = "",
    confidence: float = 0.9,
    status: str = "selected",
) -> dict[str, Any]:
    route_id = f"route:{uuid.uuid4().hex[:12]}"
    event_type = f"route.{status}"
    return RouteDecision(
        event_type=event_type,
        correlation_id=request.correlation_id,
        route_id=route_id,
        task_id=request.task_id,
        lane=lane,
        provider=provider,
        model=model,
        target_service=target_service,
        target_node_id=target_node_id,
        target_job_id=target_job_id,
        target_worker=target_worker,
        rationale=rationale,
        confidence=confidence,
    ).model_dump()


def _request_needs_tools(request: RouteRequest) -> bool:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    task_kind = str(metadata.get("task_kind") or "").strip().lower()
    capability_lane = str(metadata.get("capability_lane") or "").strip().lower()
    workflow_stage = str(metadata.get("workflow_stage") or metadata.get("stage") or "").strip().lower()
    if request.context_requirements.needs_repo or request.context_requirements.needs_external_web or request.context_requirements.needs_local_files:
        return True
    if bool(metadata.get("requires_tools")) or bool(metadata.get("evidence_required")):
        return True
    if bool(metadata.get("reviewed")) or bool(metadata.get("finalized")) or workflow_stage in {"handoff", "final", "finalized", "review_final"}:
        return True
    if capability_lane == "tool_required":
        return True
    if task_kind in {"research", "analysis", "operations", "code", "implementation", "refinement", "review", "repair"}:
        return True
    if request.tools:
        return True
    return False


def _build_tool_job_body(request: RouteRequest, selection: dict[str, Any]) -> dict[str, Any]:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    intent_text = str(request.intent.text or metadata.get("task") or metadata.get("prompt") or request.task_id or "tool task").strip()
    task_kind = str(metadata.get("task_kind") or ("analysis" if request.context_requirements.needs_external_web else "operations") or "tool_task").strip()
    workflow_stage = str(metadata.get("workflow_stage") or metadata.get("stage") or "").strip().lower()
    if not workflow_stage:
        if bool(metadata.get("finalized")) or bool(metadata.get("reviewed")):
            workflow_stage = "handoff"
        else:
            workflow_stage = "initial"
    plan_steps = metadata.get("plan_steps")
    if not isinstance(plan_steps, list) or not any(str(item).strip() for item in plan_steps):
        plan_steps = [
            "Review the current state one slice at a time.",
            "Advance the task in the smallest safe increment.",
            "Validate against the explicit acceptance criteria.",
            "Return a final handoff with risks and next steps.",
        ]
    validation_metrics = metadata.get("validation_metrics")
    if not isinstance(validation_metrics, list) or not any(str(item).strip() for item in validation_metrics):
        validation_metrics = ["acceptance_criteria_met", "regressions_checked", "handoff_ready"]
    review_checkpoints = metadata.get("review_checkpoints")
    if not isinstance(review_checkpoints, list) or not any(str(item).strip() for item in review_checkpoints):
        review_checkpoints = ["iterative review complete", "final validation complete", "handoff approved"]
    return {
        "task": intent_text,
        "repo_path": metadata.get("repo_path"),
        "repo_url": metadata.get("repo_url"),
        "branch": metadata.get("branch"),
        "task_kind": task_kind,
        "capability_lane": "tool_required",
        "requires_tools": True,
        "evidence_required": bool(metadata.get("evidence_required", True)),
        "evidence_bundle": metadata.get("evidence_bundle") or {},
        "allowed_tools": metadata.get("allowed_tools") or [],
        "plan_steps": plan_steps,
        "validation_metrics": validation_metrics,
        "review_checkpoints": review_checkpoints,
        "workflow_stage": workflow_stage,
        "allow_write": bool(metadata.get("allow_write", metadata.get("write_access", False))),
        "allow_commit": bool(metadata.get("allow_commit", metadata.get("commit_access", False))),
        "allow_network": bool(metadata.get("allow_network", True)),
        "priority": metadata.get("priority") or "repo_critical",
        "preferred_workers": selection.get("preferred_workers", []),
        "metadata": {
            **metadata,
            "source_route": "assistx",
            "task_kind": task_kind,
            "capability_lane": "tool_required",
            "requires_tools": True,
            "evidence_required": bool(metadata.get("evidence_required", True)),
            "workflow_stage": workflow_stage,
            "plan_steps": plan_steps,
            "validation_metrics": validation_metrics,
            "review_checkpoints": review_checkpoints,
        },
    }


def _select_lane_and_provider(
    request: RouteRequest,
    state: Any,
) -> dict[str, Any]:
    """Use existing PolicyEngine context to select lane/model/provider."""
    context = getattr(state, "context", None)
    providers = getattr(state, "providers", None)
    if not context or not providers:
        return {
            "lane": "local",
            "provider": "lmstudio",
            "model": "local/default",
            "target_service": None,
            "target_node_id": None,
            "rationale": "No context available; defaulting to local",
            "confidence": 0.3,
        }

    if _request_needs_tools(request):
        agent_jobs = getattr(state, "agent_jobs", None)
        if agent_jobs is not None:
            job_body = _build_tool_job_body(request, {"preferred_workers": []})
            job_request = build_agent_job_request(job_body)
            preferred_worker = job_request.preferred_workers[0] if job_request.preferred_workers else None
            return {
                "lane": "paperclip",
                "provider": "agent-jobs",
                "model": preferred_worker or "tool_orchestrator",
                "target_service": "/jobs/agent",
                "target_node_id": None,
                "target_worker": preferred_worker,
                "target_job_id": job_request.job_id,
                "preferred_workers": job_request.preferred_workers,
                "job_request": job_request,
                "plan_steps": job_request.plan_steps,
                "validation_metrics": job_request.validation_metrics,
                "workflow_stage": job_request.workflow_stage,
                "rationale": f"Tool-capable task routed to agent jobs with preferred worker {preferred_worker or 'auto'}",
                "confidence": 0.92,
            }
        return {
            "lane": "blocked",
            "provider": "none",
            "model": "none",
            "target_service": None,
            "target_node_id": None,
            "rationale": "Tool-capable task requested but no agent job manager is available",
            "confidence": 0.0,
        }

    enabled = providers.enabled()
    local_providers = [p for p in enabled if getattr(p, "quota_class", "") == "local" or (getattr(p, "base_url", "") or "").startswith("http://100.")]

    if local_providers:
        best = local_providers[0]
        models = getattr(best, "models", [])
        model_name = models[0].alias if models else "local/default"
        model_provider = models[0].provider_model if models else model_name
        return {
            "lane": "local",
            "provider": best.name,
            "model": model_provider,
            "target_service": f"{best.name}:{model_provider}",
            "target_node_id": getattr(best, "node_id", None),
            "rationale": f"Selected local provider {best.name} with model {model_provider}",
            "confidence": 0.85,
        }

    free_providers = [p for p in enabled if "free" in str(getattr(p, "quota_class", ""))]
    if free_providers:
        best = free_providers[0]
        models = getattr(best, "models", [])
        model_name = models[0].alias if models else "free/default"
        model_provider = models[0].provider_model if models else model_name
        return {
            "lane": "free_api",
            "provider": best.name,
            "model": model_provider,
            "target_service": f"{best.name}:{model_provider}",
            "target_node_id": getattr(best, "node_id", None),
            "rationale": f"Selected free provider {best.name} with model {model_provider}",
            "confidence": 0.7,
        }

    return {
        "lane": "blocked",
        "provider": "none",
        "model": "none",
        "target_service": None,
        "target_node_id": None,
        "rationale": "No eligible provider found",
        "confidence": 0.0,
    }


def register_assistx_routes(app: Any, state: Any) -> None:
    @app.post("/api/routes/request")
    async def route_request(body: RouteRequest) -> dict[str, Any]:
        selection = _select_lane_and_provider(body, state)
        lane = selection["lane"]

        if lane == "blocked":
            decision = _build_route_decision(
                body,
                lane="blocked",
                provider="none",
                model="none",
                rationale=selection["rationale"],
                confidence=0.0,
                status="blocked",
            )
        elif lane in ("local", "free_api", "paid_api", "heavy_reasoning"):
            decision = _build_route_decision(
                body,
                lane=lane,
                provider=selection["provider"],
                model=selection["model"],
                target_service=selection.get("target_service"),
                target_node_id=selection.get("target_node_id"),
                rationale=selection["rationale"],
                confidence=selection["confidence"],
                status="selected",
            )
        elif lane == "paperclip":
            job_request = selection.get("job_request")
            if job_request is None:
                job_request = build_agent_job_request(_build_tool_job_body(body, selection))
            record = state.agent_jobs.submit(job_request)
            decision = _build_route_decision(
                body,
                lane=lane,
                provider=selection["provider"],
                model=selection["model"],
                target_service=selection.get("target_service"),
                target_job_id=record.request.job_id,
                target_worker=selection.get("target_worker"),
                rationale=selection["rationale"],
                confidence=selection["confidence"],
                status="selected",
            )
        else:
            decision = _build_route_decision(
                body,
                lane=lane,
                provider=selection["provider"],
                model=selection["model"],
                rationale=selection["rationale"],
                confidence=selection["confidence"],
                status="failed",
            )

        outbox = ensure_event_outbox(state)
        outbox.enqueue(
            OutboxEvent(
                event_type=decision["event_type"],
                idempotency_key=f"{decision['event_type']}:{body.correlation_id}:{decision['route_id']}",
                payload=decision,
            )
        )

        return decision
