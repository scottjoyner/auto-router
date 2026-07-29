from __future__ import annotations

from typing import Any, cast

from fastapi import Depends, FastAPI, HTTPException

from auto_router.memory_models import (
    MemoryContext,
    MemoryIngestRequest,
    MemoryLifecycleRequest,
    MemoryOutcomeRequest,
    MemoryQuery,
)
from auto_router.memory_store import DuplicateMemoryEventError
from auto_router.security import require_admin


def register_memory_routes(app: FastAPI, state: Any) -> None:
    @app.post("/api/memory/events", dependencies=[Depends(require_admin)])
    async def ingest_memory(request: MemoryIngestRequest) -> dict[str, object]:
        try:
            return cast(dict[str, object], await state.memory_client.ingest(request))
        except DuplicateMemoryEventError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/memory/lifecycle", dependencies=[Depends(require_admin)])
    async def record_memory_lifecycle(
        request: MemoryLifecycleRequest,
    ) -> dict[str, object]:
        try:
            return cast(dict[str, object], await state.memory_client.record_lifecycle(request))
        except DuplicateMemoryEventError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/memory/outcomes", dependencies=[Depends(require_admin)])
    async def record_memory_outcome(
        request: MemoryOutcomeRequest,
    ) -> dict[str, object]:
        try:
            return cast(dict[str, object], await state.memory_client.record_outcome(request))
        except DuplicateMemoryEventError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/memory/context",
        response_model=MemoryContext,
        dependencies=[Depends(require_admin)],
    )
    async def assemble_memory_context(query: MemoryQuery) -> MemoryContext:
        return cast(MemoryContext, await state.memory_client.assemble(query))

    @app.get("/admin/memory", dependencies=[Depends(require_admin)])
    async def memory_summary() -> dict[str, object]:
        summary = cast(dict[str, object], state.memory_store.summary())
        summary["remote_url_configured"] = bool(state.memory_client.base_url)
        summary["runtime"] = state.memory_client.metrics()
        return summary
