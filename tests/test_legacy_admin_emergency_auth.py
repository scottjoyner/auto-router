"""Synthetic source-tree gate for emergency legacy admin-auth hotfix.

No ASGI lifespan, real provider calls, model loads, circuit resets or live
network requests. This branch intentionally excludes the broader PR #41
provider-catalog and deployment pipeline changes.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import auto_router.main as legacy
import auto_router.security as security
from auto_router.security import require_admin


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        security, "get_settings",
        lambda: SimpleNamespace(admin_token="synthetic-emergency-admin-only"),
    )
    # TestClient without lifespan intentionally does not start router tasks.
    return TestClient(legacy.app, raise_server_exceptions=False, follow_redirects=False)


def _methods():
    routes = [route for route in legacy.app.routes
              if isinstance(route, APIRoute) and route.path.startswith("/admin/")]
    return routes, sorted({(method, route.path) for route in routes
                           for method in route.methods or ()
                           if method not in ("HEAD", "OPTIONS")})


def _synthetic_path(template: str) -> str:
    return re.sub(r"\{[^{}]+\}", "synthetic-denied", template)


def test_every_existing_legacy_admin_method_has_direct_dependency():
    routes, methods = _methods()
    # Guard against accidentally testing a different app with too few paths.
    assert len(methods) >= 14
    assert any(method == "POST" for method, _ in methods)
    assert any("{" in path for _, path in methods)
    for route in routes:
        assert any(dep.call is require_admin for dep in route.dependant.dependencies), (
            f"unguarded legacy admin method {sorted(route.methods)} {route.path}"
        )


@pytest.mark.parametrize("credential", [None, "wrong-synthetic-value"])
def test_every_legacy_admin_method_denies_before_handler(client, credential):
    _routes, methods = _methods()
    headers = {} if credential is None else {"X-Admin-Token": credential}
    for method, template in methods:
        args = {"json": {}} if method in ("POST", "PUT", "PATCH") else {}
        response = client.request(method, _synthetic_path(template), headers=headers, **args)
        assert response.status_code == 401, (method, template, response.status_code)


def test_missing_token_configuration_also_fails_closed(monkeypatch):
    monkeypatch.setattr(security, "get_settings", lambda: SimpleNamespace(admin_token=""))
    with TestClient(legacy.app, raise_server_exceptions=False, follow_redirects=False) as client:
        for method, template in _methods()[1]:
            args = {"json": {}} if method in ("POST", "PUT", "PATCH") else {}
            result = client.request(method, _synthetic_path(template), **args)
            assert result.status_code == 401, (method, template, result.status_code)


def test_unauthorized_post_never_resets_circuits_or_reconciles_models(monkeypatch, client):
    from auto_router import model_placement

    called = []

    def refuse_reset(*_args, **_kwargs):
        called.append("reset")
        raise AssertionError("MUST_NOT_RESET")

    async def refuse_model_reconcile(*_args, **_kwargs):
        called.append("reconcile")
        raise AssertionError("MUST_NOT_LOAD_MODEL")

    monkeypatch.setattr(legacy.state.circuits, "reset", refuse_reset)
    monkeypatch.setattr(model_placement, "reconcile_once", refuse_model_reconcile)
    for path in ("/admin/circuits/reset",
                 "/admin/circuits/reset/synthetic-denied",
                 "/admin/load?apply=true"):
        assert client.post(path).status_code == 401
        assert client.post(path, headers={"X-Admin-Token": "wrong-synthetic"}).status_code == 401
    assert called == []


def test_nonadmin_health_path_was_not_moved_behind_admin_gate():
    routes = [r for r in legacy.app.routes
              if isinstance(r, APIRoute) and r.path == "/health"]
    assert len(routes) == 1
    assert not any(dep.call is require_admin for dep in routes[0].dependant.dependencies)
