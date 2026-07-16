from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from auto_router.agent_jobs import build_agent_job_request
from auto_router.event_outbox import EventOutbox, OutboxEvent
from auto_router.models import RouteDecision, RouteRequest
from auto_router.route_events import ensure_event_outbox
from auto_router.task_contract import build_task_contract

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
    return RouteDecision(
        event_type="router.route_decision",
        status=status,
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
    if request.context_requirements.needs_repo or request.context_requirements.needs_external_web or request.context_requirements.needs_local_files:
        return True
    if bool(metadata.get("requires_tools")) or bool(metadata.get("evidence_required")):
        return True
    contract = build_task_contract({**metadata, "task_kind": metadata.get("task_kind"), "workflow_stage": metadata.get("workflow_stage") or metadata.get("stage")})
    if contract["requires_tools"]:
        return True
    if contract["workflow_stage"] == "handoff":
        return True
    if request.tools:
        return True
    return False


def _build_tool_job_body(request: RouteRequest, selection: dict[str, Any]) -> dict[str, Any]:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    intent_text = str(request.intent.text or metadata.get("task") or metadata.get("prompt") or request.task_id or "tool task").strip()
    base_kind = "analysis" if request.context_requirements.needs_external_web else "operations"
    contract = build_task_contract({**metadata, "task_kind": metadata.get("task_kind") or base_kind, "workflow_stage": metadata.get("workflow_stage") or metadata.get("stage")})
    task_kind = contract["task_kind"]
    workflow_stage = contract["workflow_stage"]
    plan_steps = contract["plan_steps"]
    validation_metrics = contract["validation_metrics"]
    review_checkpoints = contract["review_checkpoints"]
    metadata_review_checkpoints = metadata.get("review_checkpoints")
    has_explicit_review_checkpoints = isinstance(metadata_review_checkpoints, list) and any(str(item).strip() for item in metadata_review_checkpoints)
    if review_checkpoints == ["reviewed by local iteration", "validated against plan", "final handoff approved"] and not has_explicit_review_checkpoints:
        review_checkpoints = ["iterative review complete", "final validation complete", "handoff approved"]
    return {
        "task": intent_text,
        "repo_path": metadata.get("repo_path"),
        "repo_url": metadata.get("repo_url"),
        "branch": metadata.get("branch"),
        "task_kind": task_kind,
        "capability_lane": contract["capability_lane"],
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
            "capability_lane": contract["capability_lane"],
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
    local_by_name = {getattr(p, "name", "").strip().lower(): p for p in local_providers if getattr(p, "name", "")}

    requested_model = str(request.model or metadata.get("model") or metadata.get("requested_model") or "").strip()
    if requested_model:
        matched_model = getattr(context, "model_for", lambda value: None)(requested_model)
        if matched_model is not None and getattr(matched_model, "is_local", False) and not getattr(matched_model, "is_blocked", False):
            provider_name = str(getattr(matched_model, "provider", "") or "").strip()
            provider = local_by_name.get(provider_name.lower())
            if provider is None:
                provider = getattr(context, "provider_for", lambda value: None)(provider_name)
            if provider is not None:
                model_name = getattr(matched_model, "provider_model", None) or getattr(matched_model, "name", None) or requested_model
                return {
                    "lane": "local",
                    "provider": provider.name,
                    "model": model_name,
                    "target_service": f"{provider.name}:{model_name}",
                    "target_node_id": getattr(provider, "node_id", None),
                    "rationale": f"Matched requested local model {requested_model} on provider {provider.name}",
                    "confidence": 0.97,
                }
        matched_provider = getattr(context, "provider_for", lambda value: None)(requested_model)
        if matched_provider is not None and getattr(matched_provider, "is_local", False):
            models = getattr(matched_provider, "models", [])
            model_name = models[0].provider_model if models else requested_model
            return {
                "lane": "local",
                "provider": matched_provider.provider,
                "model": model_name,
                "target_service": f"{matched_provider.provider}:{model_name}",
                "target_node_id": getattr(matched_provider, "node_id", None),
                "rationale": f"Matched requested local provider {requested_model}",
                "confidence": 0.9,
            }

    if local_providers:
        best = sorted(local_providers, key=lambda p: (getattr(p, "priority", 100), getattr(p, "name", "")))[0]
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
    from auto_router.settings import get_settings

    settings = get_settings()
    if not settings.fleet_dispatcher_enabled:
        print(
            "WARNING: in-process fleet/agent execution is TRANSITIONAL and currently "
            "DISABLED (AUTO_ROUTER_FLEET_DISPATCHER_ENABLED=false). Tool-capable route "
            "decisions will be marked for Paperclip dispatch, not executed in-process."
        )

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
            # In-process execution is transitional and gated. When disabled, we
            # still build the RouteDecision with target_service=/jobs/agent so the
            # canonical Paperclip execution path can pick it up, but we do NOT launch
            # a worker in this process (LLD §3.5 W-56).
            target_job_id = None
            target_worker = selection.get("target_worker")
            if settings.fleet_dispatcher_enabled:
                record = state.agent_jobs.submit(job_request)
                target_job_id = record.request.job_id
            decision = _build_route_decision(
                body,
                lane=lane,
                provider=selection["provider"],
                model=selection["model"],
                target_service=selection.get("target_service"),
                target_job_id=target_job_id,
                target_worker=target_worker,
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
