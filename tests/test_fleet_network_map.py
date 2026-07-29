from __future__ import annotations

import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_router import fleet_routes
from auto_router.context import ContextNode, ContextSnapshot


def test_network_map_uses_context_projection_and_fresh_reports(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(fleet_routes.router)
    app.state.router_state = SimpleNamespace(
        context=ContextSnapshot(
            nodes=[
                ContextNode(
                    node_id="x1-370",
                    display_name="Strix Halo",
                    capabilities={"chat", "reasoning"},
                    running=True,
                )
            ],
            metadata={"projection_status": "active"},
        )
    )
    monkeypatch.setattr(
        fleet_routes,
        "_node_reports",
        {
            "x1-370": {
                "ip": "100.64.43.123",
                "library": ["qwen-35b"],
                "loaded": ["qwen-35b"],
                "specs": {"ram_gib": 96, "cpu": "Ryzen AI 9"},
                "received_at": int(time.time()),
            }
        },
    )

    response = TestClient(app).get("/api/fleet/network-map")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "assistx-context-projection"
    assert payload["summary"]["online_count"] == 1
    assert payload["nodes"][0]["id"] == "x1-370"
    assert payload["nodes"][0]["loaded_models"] == ["qwen-35b"]


def test_benchmark_plan_uses_outcomes_and_remains_advisory(monkeypatch) -> None:
    class Ledger:
        def recent_runtime_samples(self, limit):
            return []

    class Memory:
        def recent_outcomes(self, limit):
            return []

    app = FastAPI()
    app.include_router(fleet_routes.router)
    app.state.router_state = SimpleNamespace(ledger=Ledger(), memory_store=Memory())
    monkeypatch.setattr(
        fleet_routes,
        "_node_reports",
        {
            "x1-370": {
                "hostname": "x1-370",
                "loaded": ["qwen-35b"],
                "received_at": int(time.time()),
            }
        },
    )

    payload = TestClient(app).get("/api/fleet/benchmark-plan").json()

    assert payload["advisory_only"] is True
    assert payload["auto_load_allowed"] is False
    assert payload["requests"]
    assert {row["model_id"] for row in payload["requests"]} == {"qwen-35b"}
