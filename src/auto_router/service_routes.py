from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from auto_router.service_scanner import ServiceStatusCache, scan_services


def register_service_routes(app: FastAPI, state: Any) -> None:
    if not hasattr(state, "service_status"):
        state.service_status = ServiceStatusCache()

    @app.get("/admin/services")
    async def admin_services() -> dict[str, Any]:
        services = []
        for service in state.context.all_services():
            services.append(state.service_status.merge_status(service).model_dump())
        return {"services": services, "status_cache": state.service_status.snapshot()}

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
        return {"results": [result.to_dict() for result in results]}
