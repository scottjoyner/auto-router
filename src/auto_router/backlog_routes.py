from __future__ import annotations

from typing import Any

from fastapi import FastAPI

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
    async def backlog_dry_run(payload: dict[str, Any]) -> dict[str, Any]:
        request = BacklogDryRunRequest.model_validate(payload)
        decisions = dry_run_backlog_selection(
            request,
            policy_engine=state.policy_engine,
            quota=state.quota,
            outbox=state.event_outbox,
            context=state.context,
        )
        return {
            "summary": backlog_summary(decisions),
            "decisions": [decision.to_dict() for decision in decisions],
            "outbox_summary": state.event_outbox.summary(),
        }
