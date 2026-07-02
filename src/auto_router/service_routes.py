from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, HTTPException

from auto_router.context import ContextService, ContextSnapshot, ServiceStatus
from auto_router.event_dispatcher import AssistXEventDispatcher
from auto_router.event_outbox import EventOutbox, OutboxEvent
from auto_router.service_scanner import ServiceProbeResult, ServiceStatusCache, scan_services
from auto_router.service_store import ServiceStatusStore
from auto_router.settings import get_settings


async def dispatch_outbox_batch(state: Any, limit: int = 25, dry_run: bool = False) -> dict[str, Any]:
    settings = get_settings()
    dispatcher = AssistXEventDispatcher(
        outbox=state.event_outbox,
        sink_url=settings.assistx_event_sink_url,
        timeout_seconds=settings.assistx_event_dispatch_timeout_seconds,
        max_attempts=settings.assistx_event_dispatch_max_attempts,
        basic_auth_user=settings.assistx_basic_auth_user,
        basic_auth_pass=settings.assistx_basic_auth_pass,
    )
    lock = getattr(state, "outbox_dispatch_lock", None)
    if lock is not None:
        async with lock:
            results = await dispatcher.dispatch_pending(limit=limit, dry_run=dry_run)
    else:
        results = await dispatcher.dispatch_pending(limit=limit, dry_run=dry_run)
    return {
        "configured": dispatcher.configured,
        "dry_run": dry_run,
        "results": [result.to_dict() for result in results],
        "summary": state.event_outbox.summary(),
    }


def ensure_outbox_dispatch_status(state: Any) -> dict[str, Any]:
    settings = get_settings()
    status = getattr(state, "outbox_dispatch_status", None)
    if not isinstance(status, dict):
        status = {}
        state.outbox_dispatch_status = status
    status.setdefault("configured", bool(settings.assistx_event_sink_url))
    status.setdefault("enabled", bool(settings.assistx_event_sink_url))
    status.setdefault("running", False)
    status.setdefault("status", "idle")
    status.setdefault("last_outcome", None)
    status.setdefault("last_reason", None)
    status.setdefault("last_started_at", None)
    status.setdefault("last_completed_at", None)
    status.setdefault("last_duration_ms", None)
    status.setdefault("last_error", None)
    status.setdefault("last_dry_run", False)
    status.setdefault("last_result_count", 0)
    status.setdefault("last_summary", {})
    status.setdefault("interval_seconds", float(settings.assistx_event_dispatch_interval_seconds))
    status.setdefault("timeout_seconds", float(settings.assistx_event_dispatch_timeout_seconds))
    status.setdefault("max_attempts", int(settings.assistx_event_dispatch_max_attempts))
    status.setdefault("next_run_at", None)
    return status


def build_outbox_dispatch_status(state: Any) -> dict[str, Any]:
    settings = get_settings()
    status = dict(ensure_outbox_dispatch_status(state))
    outbox_summary = state.event_outbox.summary() if hasattr(state, "event_outbox") else {}
    now = time.time()
    last_started_at = status.get("last_started_at")
    last_completed_at = status.get("last_completed_at")
    interval_seconds = float(status.get("interval_seconds") or settings.assistx_event_dispatch_interval_seconds)
    next_run_at = status.get("next_run_at")
    if next_run_at is None and last_completed_at is not None:
        next_run_at = float(last_completed_at) + interval_seconds
    next_run_in_seconds = None
    if next_run_at is not None:
        next_run_in_seconds = max(int(float(next_run_at) - now), 0)
    return {
        **status,
        "configured": bool(status.get("configured") if status.get("configured") is not None else settings.assistx_event_sink_url),
        "enabled": bool(status.get("enabled") if status.get("enabled") is not None else settings.assistx_event_sink_url),
        "running": bool(status.get("running")),
        "lock_locked": bool(getattr(getattr(state, "outbox_dispatch_lock", None), "locked", lambda: False)()),
        "pending": int(outbox_summary.get("pending") or 0),
        "retry": int(outbox_summary.get("retry") or 0),
        "delivered": int(outbox_summary.get("delivered") or 0),
        "dead_letter": int(outbox_summary.get("dead_letter") or 0),
        "total": int(outbox_summary.get("total") or 0),
        "last_started_age_seconds": max(int(now - float(last_started_at)), 0) if last_started_at is not None else None,
        "last_completed_age_seconds": max(int(now - float(last_completed_at)), 0) if last_completed_at is not None else None,
        "next_run_at": next_run_at,
        "next_run_in_seconds": next_run_in_seconds,
    }


def build_outbox_pressure_status(state: Any) -> dict[str, Any]:
    dispatch_status = build_outbox_dispatch_status(state)
    summary = dispatch_status.get("last_summary") or {}
    pending = int(dispatch_status.get("pending") or summary.get("pending") or 0)
    retry = int(dispatch_status.get("retry") or summary.get("retry") or 0)
    dead_letter = int(dispatch_status.get("dead_letter") or summary.get("dead_letter") or 0)
    total = pending + retry + dead_letter
    critical_threshold = 25
    if total == 0:
        level = "ok"
        headline = "Outbox pressure is clear"
        detail = "No pending, retry, or dead-letter outbox events are waiting to be processed."
        action = "No action required."
    elif dead_letter > 0 or total >= critical_threshold:
        level = "critical"
        headline = "Outbox pressure is high"
        detail = (
            f"The outbox has {pending} pending, {retry} retry, and {dead_letter} dead-letter events "
            "waiting to move through the AssistX sink path."
        )
        action = "Inspect /admin/outbox and /admin/outbox/dispatch before treating restart health as green."
    else:
        level = "warning"
        headline = "Outbox pressure is present"
        detail = (
            f"The outbox has {pending} pending, {retry} retry, and {dead_letter} dead-letter events "
            "waiting to move through the AssistX sink path."
        )
        action = "Clear the small backlog or confirm it is actively draining."
    return {
        **dispatch_status,
        "pressure_total": total,
        "level": level,
        "active": level != "ok",
        "critical_threshold": critical_threshold,
        "headline": headline,
        "detail": detail,
        "action": action,
    }


async def dispatch_outbox_cycle(state: Any, limit: int = 25, dry_run: bool = False, reason: str = "scheduled") -> dict[str, Any]:
    status = ensure_outbox_dispatch_status(state)
    settings = get_settings()
    started_at = time.time()
    status.update(
        {
            "configured": bool(settings.assistx_event_sink_url),
            "enabled": bool(settings.assistx_event_sink_url),
            "running": True,
            "status": "running",
            "last_outcome": "running",
            "last_reason": reason,
            "last_started_at": started_at,
            "last_error": None,
            "last_dry_run": dry_run,
            "interval_seconds": float(settings.assistx_event_dispatch_interval_seconds),
            "timeout_seconds": float(settings.assistx_event_dispatch_timeout_seconds),
            "max_attempts": int(settings.assistx_event_dispatch_max_attempts),
        }
    )
    try:
        result = await dispatch_outbox_batch(state, limit=limit, dry_run=dry_run)
    except Exception as exc:
        finished_at = time.time()
        status.update(
            {
                "running": False,
                "status": "error",
                "last_outcome": "error",
                "last_completed_at": finished_at,
                "last_duration_ms": int((finished_at - started_at) * 1000),
                "last_error": str(exc),
                "last_result_count": 0,
                "last_summary": state.event_outbox.summary() if hasattr(state, "event_outbox") else {},
                "next_run_at": finished_at + float(settings.assistx_event_dispatch_interval_seconds),
            }
        )
        raise

    finished_at = time.time()
    summary = result.get("summary", state.event_outbox.summary() if hasattr(state, "event_outbox") else {})
    status.update(
        {
            "running": False,
            "status": "idle",
            "last_outcome": "success",
            "last_completed_at": finished_at,
            "last_duration_ms": int((finished_at - started_at) * 1000),
            "last_error": None,
            "last_result_count": len(result.get("results", [])),
            "last_summary": summary,
            "next_run_at": finished_at + float(settings.assistx_event_dispatch_interval_seconds),
        }
    )
    return result


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

    @app.post("/admin/outbox/dispatch")
    async def dispatch_outbox(limit: int = 25, dry_run: bool = False) -> dict[str, Any]:
        return await dispatch_outbox_cycle(state, limit=limit, dry_run=dry_run, reason="manual")

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
