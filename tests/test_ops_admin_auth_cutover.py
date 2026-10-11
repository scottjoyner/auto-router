"""Read-only regression for ops admin routes on the strict-live mounted app.

The old deployed router already enforces auth on these two routes, so the PR
must not reintroduce anonymous access when source is rebuilt and deployed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import auto_router.ops_dashboard_routes as ops
import auto_router.security as security
from auto_router.security import require_admin


@pytest.fixture
def test_app(monkeypatch):
    token = "synthetic-ops-admin-token"
    monkeypatch.setattr(security, "get_settings", lambda: SimpleNamespace(admin_token=token))
    monkeypatch.setattr(ops, "build_ops_summary", lambda _state: {"synthetic": "summary"})
    monkeypatch.setattr(
        ops,
        "build_preflight_report",
        lambda _state, _settings: {"synthetic": "preflight"},
    )
    monkeypatch.setattr(ops, "get_settings", lambda: SimpleNamespace(admin_token=token))
    app = FastAPI()
    ops.register_ops_dashboard_routes(app, SimpleNamespace())
    return app, token


@pytest.mark.parametrize("path", ["/admin/ops/summary", "/admin/ops/preflight"])
def test_ops_route_has_explicit_admin_dependency(test_app, path):
    app, _ = test_app
    matching = [route for route in app.routes if isinstance(route, APIRoute) and route.path == path]
    assert len(matching) == 1
    assert any(dep.call is require_admin for dep in matching[0].dependant.dependencies)


@pytest.mark.parametrize("path", ["/admin/ops/summary", "/admin/ops/preflight"])
def test_anonymous_and_wrong_token_refused_before_handler(test_app, path):
    app, _ = test_app
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"X-Admin-Token": "incorrect"}).status_code == 401


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/admin/ops/summary", "summary"),
        ("/admin/ops/preflight", "preflight"),
    ],
)
def test_authorized_ops_admin_readonly_still_works(test_app, path, payload):
    app, token = test_app
    with TestClient(app) as client:
        response = client.get(path, headers={"X-Admin-Token": token})
    assert response.status_code == 200
    assert response.json() == {"synthetic": payload}
    assert token not in response.text
