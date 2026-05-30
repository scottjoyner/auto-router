from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from auto_router.context import ContextService, ContextSnapshot, ServiceStatus
from auto_router.service_scanner import ServiceProbeResult, ServiceStatusCache, scan_services


def register_service_routes(app: FastAPI, state: Any) -> None:
    if not hasattr(state, "service_status"):
        state.service_status = ServiceStatusCache()

    @app.get("/admin/services")
    async def admin_services() -> dict[str, Any]:
        services = merged_services(state)
        return {
            "services": [service.model_dump() for service in services],
            "status_cache": state.service_status.snapshot(),
            "summary": service_summary(services),
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
        state.context = apply_service_results_to_context(state.context, results)
        if hasattr(state, "policy_engine"):
            state.policy_engine.context = state.context
        merged = merged_services(state)
        return {
            "results": [result.to_dict() for result in results],
            "summary": service_summary(merged),
        }


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
