from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_router import fleet_routes


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(fleet_routes.router)
    fleet_routes._node_reports.clear()
    with TestClient(app) as test_client:
        yield test_client
    fleet_routes._node_reports.clear()


REPORTER_PAYLOAD = {
    "hostname": "scott-OptiPlex-9030-AIO",
    "host_name": "scott-OptiPlex-9030-AIO",
    "library": [{"id": "model-a"}, {"id": "model-b"}],
    "loaded": ["model-a"],
    "specs": {"cpu_cores": 4, "system_ram_gib": 7.65},
    "health": {"status": "healthy"},
    "disk": {"root_use_pct": 26.1},
}

AGENT_PAYLOAD = {
    "node_id": "scott-OptiPlex-9030-AIO",
    "capabilities": ["llm", "lmstudio", "python3"],
    "models": ["model-a"],
    "healthy": True,
    "reported_at": "2026-10-03T00:00:00+00:00",
}


def test_agent_node_id_resolves_to_real_hostname(client):
    """The agent sends node_id; the router must not file it under 'unknown'."""
    response = client.post("/api/fleet/node-report", json=AGENT_PAYLOAD)
    assert response.status_code == 200
    assert response.json()["hostname"] == "scott-OptiPlex-9030-AIO"
    assert "unknown" not in fleet_routes._node_reports


def test_agent_does_not_erase_reporter_inventory(client):
    """Reporter publishes inventory; a later sparse agent report must not wipe it."""
    client.post("/api/fleet/node-report", json=REPORTER_PAYLOAD)
    client.post("/api/fleet/node-report", json=AGENT_PAYLOAD)

    report = fleet_routes._node_reports["scott-OptiPlex-9030-AIO"]
    assert len(report["library"]) == 2
    assert report["loaded"] == ["model-a"]
    assert report["specs"]["cpu_cores"] == 4
    assert report["health"] == {"status": "healthy"}
    assert report["capabilities"] == ["llm", "lmstudio", "python3"]


def test_reporter_does_not_erase_agent_capabilities(client):
    """Reverse order: the reporter must not drop capabilities the agent sent."""
    client.post("/api/fleet/node-report", json=AGENT_PAYLOAD)
    client.post("/api/fleet/node-report", json=REPORTER_PAYLOAD)

    report = fleet_routes._node_reports["scott-OptiPlex-9030-AIO"]
    assert report["capabilities"] == ["llm", "lmstudio", "python3"]
    assert len(report["library"]) == 2


def test_explicit_empty_list_clears_a_field(client):
    """Presence-based merge, so an explicit empty list still means 'nothing loaded'."""
    client.post("/api/fleet/node-report", json=REPORTER_PAYLOAD)
    client.post("/api/fleet/node-report", json={**REPORTER_PAYLOAD, "loaded": []})

    report = fleet_routes._node_reports["scott-OptiPlex-9030-AIO"]
    assert report["loaded"] == []
    assert len(report["library"]) == 2


def test_distinct_nodes_do_not_share_a_slot(client):
    """Two agents that omit hostname must not clobber one another."""
    client.post("/api/fleet/node-report", json={**AGENT_PAYLOAD, "node_id": "lenovo"})
    client.post("/api/fleet/node-report", json={**AGENT_PAYLOAD, "node_id": "destroyer"})

    assert fleet_routes._node_reports["lenovo"]["capabilities"] == ["llm", "lmstudio", "python3"]
    assert fleet_routes._node_reports["destroyer"]["capabilities"] == ["llm", "lmstudio", "python3"]


def test_received_at_refreshes_on_every_report(client):
    client.post("/api/fleet/node-report", json=REPORTER_PAYLOAD)
    first = fleet_routes._node_reports["scott-OptiPlex-9030-AIO"]["received_at"]
    client.post("/api/fleet/node-report", json=AGENT_PAYLOAD)
    second = fleet_routes._node_reports["scott-OptiPlex-9030-AIO"]["received_at"]
    assert second >= first


def test_publish_emits_the_merged_record(client):
    """SSE/redis fan-out must carry the merged view, not the last raw payload,
    or live-tailnet consumers see a half-populated node every time the agent
    publishes on its shorter interval."""
    queue: asyncio.Queue = asyncio.Queue()
    fleet_routes._sse_subscribers.append(queue)
    try:
        client.post("/api/fleet/node-report", json=REPORTER_PAYLOAD)
        client.post("/api/fleet/node-report", json=AGENT_PAYLOAD)
        queue.get_nowait()
        published = queue.get_nowait()
    finally:
        fleet_routes._sse_subscribers.clear()

    assert published["hostname"] == "scott-OptiPlex-9030-AIO"
    assert len(published["library"]) == 2
    assert published["capabilities"] == ["llm", "lmstudio", "python3"]


def test_report_without_any_identity_key_does_not_crash(client):
    """A malformed client must still get a 200 and a well-formed bucket."""
    response = client.post("/api/fleet/node-report", json={"library": [{"id": "m"}]})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "hostname": "unknown"}
    assert fleet_routes._node_reports["unknown"]["library"] == [{"id": "m"}]


def test_nodes_view_reflects_merged_state(client):
    client.post("/api/fleet/node-report", json=REPORTER_PAYLOAD)
    client.post("/api/fleet/node-report", json=AGENT_PAYLOAD)

    body = client.get("/api/fleet/nodes").json()
    node = next(n for n in body["nodes"] if n["hostname"] == "scott-OptiPlex-9030-AIO")
    assert node["capabilities"] == ["llm", "lmstudio", "python3"]
    assert len(node["library"]) == 2
    assert node["health"] == {"status": "healthy"}


def test_long_dead_entries_are_pruned(client, monkeypatch):
    """_node_reports is module-level and never shed entries, so every hostname a
    node ever used grew the dict forever. Pruning must keep recent entries."""
    now = int(time.time())
    monkeypatch.setattr(fleet_routes, "_now_seconds", lambda: now, raising=False)

    client.post("/api/fleet/node-report", json=REPORTER_PAYLOAD)
    fleet_routes._node_reports["ghost"] = {
        "hostname": "ghost",
        "received_at": now - fleet_routes._PRUNE_SECONDS - 60,
    }
    client.post("/api/fleet/node-report", json=AGENT_PAYLOAD)

    assert "ghost" not in fleet_routes._node_reports
    assert "scott-OptiPlex-9030-AIO" in fleet_routes._node_reports


def test_recently_stale_entries_survive_pruning(client, monkeypatch):
    """Pruning is a memory backstop, not a freshness policy -- the /nodes view
    already filters on _STALE_SECONDS, so entries inside the grace window must
    stay put rather than changing what other endpoints return."""
    now = int(time.time())
    monkeypatch.setattr(fleet_routes, "_now_seconds", lambda: now, raising=False)

    client.post("/api/fleet/node-report", json=REPORTER_PAYLOAD)
    fleet_routes._node_reports["recently-off"] = {
        "hostname": "recently-off",
        "received_at": now - fleet_routes._STALE_SECONDS - 5,
    }
    client.post("/api/fleet/node-report", json=AGENT_PAYLOAD)

    assert "recently-off" in fleet_routes._node_reports

def test_agent_heartbeat_cannot_extend_stale_model_evidence(client, monkeypatch):
    """A frequently polling worker must not keep a dead model claim fresh."""
    clock = [int(time.time())]
    monkeypatch.setattr(fleet_routes, "_now_seconds", lambda: clock[0])
    client.post("/api/fleet/node-report", json=REPORTER_PAYLOAD)
    report = fleet_routes._node_reports["scott-OptiPlex-9030-AIO"]
    assert report["loaded"] == ["model-a"]
    clock[0] += fleet_routes._STALE_SECONDS + 1
    client.post("/api/fleet/node-report", json=AGENT_PAYLOAD)
    report = fleet_routes._node_reports["scott-OptiPlex-9030-AIO"]
    assert report["loaded"] == []
    assert report["library"] == []
    assert report["health"] == {}
    assert report["capabilities"] == ["llm", "lmstudio", "python3"]
    assert "loaded" not in report["field_received_at"]