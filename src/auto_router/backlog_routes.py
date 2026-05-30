from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from auto_router.assistx_tasks import AssistXTaskClient
from auto_router.backlog_scheduler import (
    BacklogDryRunRequest,
    backlog_summary,
    dry_run_backlog_selection,
)
from auto_router.event_outbox import EventOutbox
from auto_router.settings import get_settings


def register_backlog_routes(app: FastAPI, state: Any) -> None:
    if not hasattr(state, "event_outbox"):
        state.event_outbox = EventOutbox(get_settings().database_url)

    @app.post("/admin/backlog/dry-run")
    async def backlog_dry_run(
        payload: dict[str, Any],
        source: str = "manual",
        limit: int = 25,
        queue: str = "backlog",
    ) -> dict[str, Any]:
        request = BacklogDryRunRequest.model_validate(payload)
        assistx_info: dict[str, Any] | None = None
        if source == "assistx":
            settings = get_settings()
            client = AssistXTaskClient(
                base_url=settings.assistx_tasks_url,
                timeout_seconds=settings.assistx_tasks_timeout_seconds,
            )
            if not client.configured:
                raise HTTPException(status_code=400, detail={"error": "AUTO_ROUTER_ASSISTX_TASKS_URL is not configured"})
            fetched = await client.fetch_backlog_candidates(limit=limit, queue=queue, dry_run=True)
            request.tasks = fetched
            assistx_info = {"configured": True, "fetched": len(fetched), "queue": queue, "limit": limit}
        decisions = dry_run_backlog_selection(
            request,
            policy_engine=state.policy_engine,
            quota=state.quota,
            outbox=state.event_outbox,
            context=state.context,
        )
        return {
            "source": source,
            "assistx": assistx_info,
            "summary": backlog_summary(decisions),
            "decisions": [decision.to_dict() for decision in decisions],
            "outbox_summary": state.event_outbox.summary(),
        }

    @app.get("/admin/backlog/assistx/config")
    async def backlog_assistx_config() -> dict[str, Any]:
        settings = get_settings()
        return {
            "configured": bool(settings.assistx_tasks_url),
            "tasks_url": settings.assistx_tasks_url,
            "timeout_seconds": settings.assistx_tasks_timeout_seconds,
        }
