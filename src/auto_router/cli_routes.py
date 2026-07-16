from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI

from auto_router.cli_discovery import discover_cli_tools
from auto_router.event_outbox import EventOutbox, OutboxEvent
from auto_router.settings import get_settings


def register_cli_routes(app: FastAPI, state: Any) -> None:
    if not hasattr(state, "cli_discovery"):
        state.cli_discovery = []
    if not hasattr(state, "event_outbox"):
        state.event_outbox = EventOutbox(get_settings().database_url)

    @app.get("/admin/agent-clis")
    async def admin_agent_clis() -> dict[str, Any]:
        return {"clis": state.cli_discovery}

    @app.post("/admin/agent-clis/discover")
    async def discover_agent_clis(enqueue_events: bool = True) -> dict[str, Any]:
        results = await discover_cli_tools()
        payloads = [result.to_dict() for result in results]
        state.cli_discovery = payloads
        event_ids: list[str] = []
        if enqueue_events:
            event_ids = enqueue_cli_discovery_events(state, payloads)
        return {
            "clis": payloads,
            "outbox_event_ids": event_ids,
            "summary": cli_summary(payloads),
            "outbox_summary": state.event_outbox.summary(),
        }


def cli_summary(results: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(results),
        "installed": sum(1 for item in results if item.get("installed")),
        "runnable": sum(1 for item in results if item.get("runnable")),
        "missing": sum(1 for item in results if not item.get("installed")),
    }


def enqueue_cli_discovery_events(state: Any, results: list[dict[str, Any]]) -> list[str]:
    event_ids: list[str] = []
    context_revision = getattr(getattr(state, "context", None), "revision", "unknown")
    context_source = getattr(getattr(state, "context", None), "source", "unknown")
    for result in results:
        idempotency_key = (
            f"router.agent_cli.discovered:{result.get('node_id')}:{result.get('name')}:"
            f"{result.get('checked_at')}:{result.get('installed')}:{result.get('runnable')}"
        )
        event = OutboxEvent(
            event_type="router.agent_cli.discovered",
            idempotency_key=idempotency_key,
            payload={
                **result,
                "correlation_id": str(uuid.uuid4()),
                "context_revision": context_revision,
                "context_source": context_source,
            },
        )
        event_ids.append(state.event_outbox.enqueue(event))
    return event_ids
