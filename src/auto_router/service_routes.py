from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from auto_router.context import ContextService, ContextSnapshot, ServiceStatus
from auto_router.event_outbox import EventOutbox, OutboxEvent
from auto_router.service_scanner import ServiceProbeResult, ServiceStatusCache, scan_services
from auto_router.service_store import ServiceStatusStore
from auto_router.settings import get_settings


def register_service_routes(app: FastAPI, state: Any) -> None:
    if not hasattr(state, "service_status"):
        state.service_status = ServiceStatusCache()
    if not hasattr(state, "service_store"):
        state.service_store = ServiceStatusStore(get_settings().database_url)
        hydrate_service_cache_from_store(state)
    if not hasattr(state, "event_outbox"):
        state.event_outbox = EventOutbox(get_settings().database_url)

    @app.get("/admin/services")
    async def admin_services(limit: int = 50) -> dict[str, Any]:
        services = merged_services(state)
        return {
            "services": [service.model_dump() for service in services],
            "status_cache": state.service_status.snapshot(),
            "summary": service_summary(services),
            "stored_summary": state.service_store.summary(),
            "recent_events": state.service_store.recent_results(limit=limit),
        }

    @app.post("/admin/services/scan")
    async def scan_registered_services(
        allow_external: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        services = state.context.all_services()
        if not services:
            raise HTTPException(status_code=404, detail={"error": "no services registered in context"})
        results = await scan_services(
            services,
            timeout_seconds=2.0,
            allow_external=allow_external,
            limit=limit,
        )
        for result in results:
            state.service_status.update(result)
        state.service_store.save_results(results)
        event_ids = enqueue_service_snapshot_events(state, results)
        state.context = apply_service_results_to_context(state.context, results)
        if hasattr(state, "policy_engine"):
            state.policy_engine.context = state.context
        merged = merged_services(state)
        return {
            "results": [result.to_dict() for result in results],
            "summary": service_summary(merged),
            "stored_summary": state.service_store.summary(),
            "outbox_event_ids": event_ids,
            "outbox_summary": state.event_outbox.summary(),
        }

    @app.get("/admin/outbox")
    async def admin_outbox(limit: int = 100) -> dict[str, Any]:
        return {
            "summary": state.event_outbox.summary(),
            "pending": state.event_outbox.pending(limit=limit),
            "recent": state.event_outbox.recent(limit=limit),
        }

    @app.post("/admin/outbox/{event_id}/delivered")
    async def mark_outbox_delivered(event_id: str) -> dict[str, Any]:
        state.event_outbox.mark_delivered(event_id)
        return {"event_id": event_id, "status": "delivered", "summary": state.event_outbox.summary()}

    @app.post("/admin/outbox/{event_id}/failed")
    async def mark_outbox_failed(event_id: str, error: str = "manual failure", retry: bool = True) -> dict[str, Any]:
        state.event_outbox.mark_failed(event_id, error=error, retry=retry)
        return {"event_id": event_id, "status": "retry" if retry else "dead_letter", "summary": state.event_outbox.summary()}


def hydrate_service_cache_from_store(state: Any) -> None:
    latest = state.service_store.latest_results()
    for result in latest:
        state.service_status.update(result)
    if latest and hasattr(state, "context"):
        state.context = apply_service_results_to_context(state.context, latest)
        if hasattr(state, "policy_engine"):
            state.policy_engine.context = state.context


def enqueue_service_snapshot_events(state: Any, results: list[ServiceProbeResult]) -> list[str]:
    event_ids: list[str] = []
    revision = getattr(state.context, "revision", "unknown")
    source = getattr(state.context, "source", "unknown")
    for result in results:
        idempotency_key = f"router.service_snapshot.recorded:{result.service_id}:{result.checked_at}:{result.status.value}"
        event = OutboxEvent(
            event_type="router.service_snapshot.recorded",
            idempotency_key=idempotency_key,
            payload={
                "service_id": result.service_id,
                "name": result.name,
                "url": result.url,
                "status": result.status.value,
                "checked_at": result.checked_at,
                "latency_ms": result.latency_ms,
                "status_code": result.status_code,
                "error": result.error,
                "skipped": result.skipped,
                "reason": result.reason,
                "context_revision": revision,
                "context_source": source,
            },
        )
        event_ids.append(state.event_outbox.enqueue(event))
    return event_ids


def merged_services(state: Any) -> list[ContextService]:
    return [state.service_status.merge_status(service) for service in state.context.all_services()]


def service_summary(services: list[ContextService]) -> dict[str, int]:
    summary = {status.value: 0 for status in ServiceStatus}
    summary["total"] = len(services)
    for service in services:
        summary[service.status.value] = summary.get(service.status.value, 0) + 1
    return summary


def apply_service_results_to_context(
    context: ContextSnapshot,
    results: list[ServiceProbeResult],
) -> ContextSnapshot:
    updates = {result.service_id: result.status for result in results}
    if not updates:
        return context
    return context.model_copy(
        update={
            "services": [_merge_service(service, updates) for service in context.services],
            "nodes": [
                node.model_copy(
                    update={"services": [_merge_service(service, updates) for service in node.services]}
                )
                for node in context.nodes
            ],
            "providers": [
                provider.model_copy(
                    update={"services": [_merge_service(service, updates) for service in provider.services]}
                )
                for provider in context.providers
            ],
        }
    )


def _merge_service(service: ContextService, updates: dict[str, ServiceStatus]) -> ContextService:
    status = updates.get(service.service_id)
    if status is None:
        return service
    return service.model_copy(update={"status": status})
