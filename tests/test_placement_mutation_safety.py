"""Fail-closed placement and admin mutation authorization regressions."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute

from auto_router import model_placement as placement

PROVIDER = "unit-lmstudio"
MODEL = "known-model"
OTHER = "unmanaged-model"


def make_state(*, records=None, ok=True, age=1, expires_in=120, missing=False):
    now = int(time.time())
    snapshots = [] if missing else [SimpleNamespace(
        provider=PROVIDER, ok=ok, fetched_at=now - age,
        expires_at=now + expires_in, latency_ms=1,
        models=records if records is not None else [],
    )]
    return SimpleNamespace(
        model_registry=SimpleNamespace(latest_inventory=lambda: snapshots),
        providers=SimpleNamespace(providers=[SimpleNamespace(
            name=PROVIDER, type="lmstudio", base_url="http://localhost.invalid/v1")]),
    )


@pytest.fixture(autouse=True)
def one_desired_model(monkeypatch):
    monkeypatch.setattr(placement, "DESIRED_PLACEMENTS", [
        placement.DesiredLoad(PROVIDER, MODEL, 8192, "negative_regression")])
    monkeypatch.setattr(placement, "UNLOAD_ENABLED", False)
    monkeypatch.setattr(placement, "UNLOAD_ALLOWED_KEYS", set())


@pytest.mark.parametrize("state", [
    make_state(missing=True),
    make_state(ok=False, records=[{"id": MODEL, "loaded": False}]),
    make_state(age=75, records=[{"id": MODEL, "loaded": False}]),
    make_state(expires_in=-1, records=[{"id": MODEL, "loaded": False}]),
    make_state(records=[]),
    make_state(records=[{"id": MODEL, "residency": "unknown"}]),
    make_state(records=[{"id": OTHER, "loaded": False}]),
    make_state(records=[{"id": MODEL, "loaded": True}]),
], ids=["absent-provider", "unreachable", "too-old", "expired", "empty-catalog",
        "unknown-residency", "missing-model", "already-loaded"])
def test_no_load_without_fresh_explicit_nonresidency(state):
    assert not placement.compute_plan(state)["loads_needed"]


def test_fresh_measured_unloaded_can_be_planned():
    plan = placement.compute_plan(make_state(records=[{"id": MODEL, "loaded": False}]))
    assert len(plan["loads_needed"]) == 1
    assert plan["loads_needed"][0]["model_key"] == MODEL


@pytest.mark.asyncio
async def test_unknown_residency_apply_never_calls_runtime(monkeypatch):
    async def refuse(*args, **kwargs):
        raise AssertionError("mutating runtime must never be called")
    monkeypatch.setattr(placement, "_load_model", refuse)
    monkeypatch.setattr(placement, "_unload_model", refuse)
    plan = await placement.reconcile_once(
        make_state(records=[{"id": MODEL, "residency": "unknown"}]), apply=True)
    assert not plan["actions_taken"]


@pytest.mark.parametrize("allowlist,expected", [(set(), []), ({OTHER}, [OTHER]), ({MODEL}, [])])
def test_unloads_require_allowlist_and_fresh_measurement(monkeypatch, allowlist, expected):
    monkeypatch.setattr(placement, "UNLOAD_ENABLED", True)
    monkeypatch.setattr(placement, "UNLOAD_ALLOWED_KEYS", allowlist)
    state = make_state(records=[{"id": OTHER, "loaded": True}])
    assert [a["model_key"] for a in placement.compute_plan(state)["unloads_needed"]] == expected
    stale = make_state(age=75, records=[{"id": OTHER, "loaded": True}])
    assert not placement.compute_plan(stale)["unloads_needed"]


def test_mutating_admin_routes_require_auth_dependency():
    from auto_router.main import app
    from auto_router.security import require_admin
    paths = {"/admin/load", "/admin/circuits/reset", "/admin/circuits/reset/{owner:path}"}
    for path in paths:
        routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path == path]
        assert len(routes) == 1, path
        assert "POST" in routes[0].methods
        auth_dependencies = [dependency.call for dependency in routes[0].dependant.dependencies]
        assert require_admin in auth_dependencies, path


def test_mutating_admin_routes_deny_unauthenticated_http():
    # In-process ASGI only: no request reaches a live router or fleet endpoint.
    from fastapi.testclient import TestClient

    from auto_router.main import app

    with TestClient(app) as client:
        for path in ("/admin/load", "/admin/circuits/reset",
                     "/admin/circuits/reset/unit-test"):
            response = client.post(path)
            assert response.status_code == 401, path
