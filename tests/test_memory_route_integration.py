from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_router.assistx_routes import register_assistx_routes
from auto_router.event_outbox import EventOutbox
from auto_router.memory_models import MemoryContext


class _MemoryClient:
    def __init__(self) -> None:
        self.queries = []

    async def assemble(self, query):
        self.queries.append(query)
        return MemoryContext(
            query=query,
            context_text="Relevant fleet experience memory:\n- AssistX owns task state.",
            estimated_tokens=14,
            backend="sqlite-lexical",
            degraded=True,
        )


class _Providers:
    def enabled(self):
        return []


class _AgentJobs:
    def __init__(self) -> None:
        self.workers = []


def test_assistx_route_runs_memory_preflight_before_selection(tmp_path) -> None:
    memory_client = _MemoryClient()
    state = SimpleNamespace(
        context=SimpleNamespace(),
        providers=_Providers(),
        agent_jobs=_AgentJobs(),
        memory_client=memory_client,
        event_outbox=EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}"),
    )
    app = FastAPI()
    register_assistx_routes(app, state)

    response = TestClient(app).post(
        "/api/routes/request",
        json={
            "correlation_id": "corr-memory-1",
            "task_id": "task-memory-1",
            "intent": {"text": "Inspect AssistX task ownership"},
            "metadata": {
                "repository": "scottjoyner/auto-router",
                "requires_tools": True,
            },
        },
    )

    assert response.status_code == 200
    assert len(memory_client.queries) == 1
    assert memory_client.queries[0].repository == "scottjoyner/auto-router"
    assert memory_client.queries[0].task_id == "task-memory-1"

