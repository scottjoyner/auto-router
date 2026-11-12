"""Fail-closed isolation of Joyner GPU heartbeat and model catalog. No real POSTs."""
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
    fleet_routes._gpu_presence_reports.clear()
    with TestClient(app) as t:
        yield t
    fleet_routes._node_reports.clear()
    fleet_routes._gpu_presence_reports.clear()


def inventory():
    return {
        "hostname": "joyner",
        "library": [{"id": "joyner-toolcall", "path": ""}],
        "loaded": [],
        "power_profile": "daily",
        "power_model_class": "full",
        "specs": {"system_ram_gib": 14.75, "gpu": "AMD"},
    }


def heartbeat():
    return {
        "hostname": "joyner",
        "os": "Linux",
        "library": [],
        "loaded": [],
        "capabilities": [],
        "specs": {
            "platform": "Linux",
            "hostname": "joyner",
            "gpu": "",
            "gpu_backend": "Vulkan RADV",
            "gpu_device": "0000:03:00.0",
            "gpu_vram_gib": 0,
            "gpu_vram_used_gib": 0,
            "system_ram_gib": 14.75,
        },
        "health": {
            "status": "degraded",
            "gpu_canary_reachable_and_guarded": False,
            "dispatch_admitted": False,
            "toolcall_3b_unadmitted": True,
            "toolcall_3b_model_alias": "toolcall-v5-3b-combined-r2-f16",
            "other_secret": "do_not_echo_me",
        },
    }


def test_presence_does_not_overwrite_model_inventory(client):
    first = client.post("/api/fleet/node-report", json=inventory())
    assert first.status_code == 200
    before = fleet_routes._node_reports["joyner"].copy()
    later = client.post("/api/fleet/node-report", json=heartbeat())
    assert later.status_code == 200
    assert later.json()["disposition"] == "gpu_presence_only"
    assert fleet_routes._node_reports["joyner"] == before
    assert client.get("/api/fleet/node/joyner").json()["library"] == inventory()["library"]
    assert client.get("/api/fleet/nodes").json()["nodes"][0]["library"] == inventory()["library"]


def test_presence_first_then_inventory_does_not_clear_library(client):
    client.post("/api/fleet/node-report", json=heartbeat())
    assert "joyner" not in fleet_routes._node_reports
    client.post("/api/fleet/node-report", json=inventory())
    assert fleet_routes._node_reports["joyner"]["library"] == inventory()["library"]


def test_repeated_heartbeat_does_not_refresh_model_report_timestamp(client):
    client.post("/api/fleet/node-report", json=inventory())
    original=fleet_routes._node_reports["joyner"]
    received=original["received_at"]
    client.post("/api/fleet/node-report", json=heartbeat())
    client.post("/api/fleet/node-report", json=heartbeat())
    assert fleet_routes._node_reports["joyner"] is original
    assert fleet_routes._node_reports["joyner"]["received_at"] == received


def test_presence_never_notifies_model_catalog_sse(client):
    queue = asyncio.Queue()
    fleet_routes._sse_subscribers.append(queue)
    try:
        client.post("/api/fleet/node-report", json=inventory())
        first=queue.get_nowait()
        assert first["library"]
        client.post("/api/fleet/node-report", json=heartbeat())
        assert queue.empty()
    finally:
        fleet_routes._sse_subscribers.remove(queue)


def test_presences_are_no_model_authority(client):
    client.post("/api/fleet/node-report", json=heartbeat())
    data = client.get("/api/fleet/presence/joyner").json()
    assert data["status"] == "UNVERIFIED_GPU_PRESENCE"
    assert data["publisher_authenticated"] is False
    assert data["model_inventory_updated"] is False
    assert data["resident_model_attested"] is False
    assert data["dispatch_admitted"] is False
    assert "library" not in data
    assert "health" not in data
    assert "specs" not in data
    assert "do_not_echo_me" not in str(data)


def test_presence_without_matching_heartbeat_does_not_emit_fake_presence(client):
    client.post("/api/fleet/node-report", json=inventory())
    d=client.get("/api/fleet/presence/joyner").json()
    assert d["status"] == "NO_RECENT_PRESENCE_OBSERVATION"
    assert d["dispatch_admitted"] is False


def test_presence_after_expiry_does_not_claim_current(client):
    now=int(time.time())
    fleet_routes._gpu_presence_reports["joyner"] = {
        "received_at": now-3900, "status":"UNVERIFIED_GPU_PRESENCE",
    }
    d=client.get("/api/fleet/presence/joyner").json()
    assert d["status"] == "PRESENCE_OBSERVATION_EXPIRED"
    assert d["dispatch_admitted"] is False


def test_changed_heartbeat_with_loaded_claim_cannot_get_presence_marker(client):
    d=heartbeat()
    d["loaded"]=["forged-resident"]
    assert not fleet_routes._is_unadmitted_gpu_presence(d)


def test_heartbeat_requires_exact_guard_fields(client):
    for key,value in [
        ("dispatch_admitted", True),
        ("dispatch_admitted", None),
        ("toolcall_3b_unadmitted", False),
        ("gpu_canary_reachable_and_guarded", 0),
    ]:
        d=heartbeat()
        d["health"][key]=value
        assert not fleet_routes._is_unadmitted_gpu_presence(d)


def test_other_hosts_not_classified_as_joyner(client):
    d=heartbeat()
    d["hostname"]="xwing"
    assert not fleet_routes._is_unadmitted_gpu_presence(d)


def test_arbitrary_generic_empty_report_behavior_not_changed(client):
    client.post("/api/fleet/node-report", json=inventory())
    d={"hostname":"joyner","library":[],"loaded":[]}
    response=client.post("/api/fleet/node-report",json=d)
    assert response.status_code==200
    assert response.json()=={"ok":True,"hostname":"joyner"}
    assert fleet_routes._node_reports["joyner"]["library"]==[]
    # This remains an unauthenticated reporting endpoint. A separate owner
    # authenticated protocol is still required for production model authority.


def test_presence_report_cannot_be_used_as_loaded_model_even_if_empty_inventory(client):
    client.post("/api/fleet/node-report",json=heartbeat())
    assert "joyner" not in fleet_routes._node_reports
    assert client.get("/api/fleet/nodes").json()["count"] == 0


def test_expiry_prunes_presence_at_next_presence(client):
    fleet_routes._gpu_presence_reports["expired"] = {
        "received_at": int(time.time()) - 5000,
        "status": "UNVERIFIED_GPU_PRESENCE",
    }
    client.post("/api/fleet/node-report",json=heartbeat())
    assert "joyner" in fleet_routes._gpu_presence_reports
    assert "expired" not in fleet_routes._gpu_presence_reports
    assert len(fleet_routes._gpu_presence_reports)==1


def test_presence_does_not_publish_to_redis_or_sse(client):
    class FakeRedis:
        def __init__(self):
            self.published = []
        async def publish(self, topic, payload):
            self.published.append((topic, payload))

    redis = FakeRedis()
    client.app.state.redis = redis
    queue = asyncio.Queue()
    fleet_routes._sse_subscribers.append(queue)
    try:
        client.post("/api/fleet/node-report", json=inventory())
        assert len(redis.published) == 1
        assert not queue.empty()
        queue.get_nowait()
        client.post("/api/fleet/node-report", json=heartbeat())
        assert len(redis.published) == 1
        assert queue.empty()
    finally:
        fleet_routes._sse_subscribers.remove(queue)


def test_presence_store_is_bounded():
    original = dict(fleet_routes._gpu_presence_reports)
    try:
        fleet_routes._gpu_presence_reports.clear()
        for i in range(fleet_routes._PRESENCE_MAX_HOSTS + 30):
            fleet_routes._save_unadmitted_gpu_presence(
                f"test{i}", observed_at=1000 + i,
            )
        assert len(fleet_routes._gpu_presence_reports) <= fleet_routes._PRESENCE_MAX_HOSTS
        assert "test0" not in fleet_routes._gpu_presence_reports
    finally:
        fleet_routes._gpu_presence_reports.clear()
        fleet_routes._gpu_presence_reports.update(original)


def test_unadmitted_model_bearing_canary_is_not_silently_accepted(client):
    d = heartbeat()
    d["library"] = [{"id": "unauthorized-inventory"}]
    assert not fleet_routes._is_unadmitted_gpu_presence(d)
    # The original ingest path remains untrusted. Do not treat this test as
    # authorization: separate reporter authentication is a release prerequisite.


def test_no_raw_model_paths_in_presence_response(client):
    d=heartbeat()
    d["specs"]["secret_path"]="/home/joyner/.config/API_KEY"
    client.post("/api/fleet/node-report",json=d)
    out=client.get("/api/fleet/presence/joyner").text
    assert "API_KEY" not in out
    assert "toolcall-v5" not in out