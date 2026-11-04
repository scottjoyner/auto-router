"""Synthetic, no-lifespan authentication audit of every Auto-Router /admin/* route.

No live service, outbound network access, state mutation or production token.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "auto_router"
ADMIN_TOKEN = "synthetic-route-surface-test-only-token"


def _literal_admin_decorators():
    results = []
    for path in SOURCE.glob("*.py"):
        module = ast.parse(path.read_text())
        for node in ast.walk(module):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call):
                    continue
                if not isinstance(deco.func, ast.Attribute):
                    continue
                if deco.func.attr not in ("get", "post", "put", "patch", "delete"):
                    continue
                if not deco.args or not isinstance(deco.args[0], ast.Constant):
                    continue
                value = deco.args[0].value
                if not isinstance(value, str) or not value.startswith("/admin/"):
                    continue
                dep = next(
                    (kw.value for kw in deco.keywords if kw.arg == "dependencies"),
                    None,
                )
                has_auth = (
                    isinstance(dep, (ast.List, ast.Tuple))
                    and any(
                        isinstance(entry, ast.Call)
                        and isinstance(entry.func, ast.Name)
                        and entry.func.id == "Depends"
                        and len(entry.args) == 1
                        and isinstance(entry.args[0], ast.Name)
                        and entry.args[0].id == "require_admin"
                        for entry in dep.elts
                    )
                )
                results.append((path.name, deco.func.attr, value, has_auth))
    return results


def test_exact_admin_decorator_contract_fails_closed_on_future_routes():
    routes = _literal_admin_decorators()
    assert len(routes) == 34, "review required: route inventory changed"
    failures = [entry[:3] for entry in routes if not entry[3]]
    assert failures == [], f"admin endpoint bypasses require_admin: {failures}"


def test_reconciled_inherited_admin_routes_use_gated_decorators():
    # main_live imports main and carries the same routes in the reconciled
    # process. Source-level audit covers legacy route modules not mounted.
    names = {value for name, _, value, auth in _literal_admin_decorators()
             if name in ("main.py", "main_live.py") and auth}
    assert "/admin/providers" in names
    assert "/admin/context/graph" in names
    assert "/admin/admission" in names
    assert "/admin/circuits/reset" in names
    assert "/admin/runtime-projection" in names


@pytest.fixture
def fixture_client(monkeypatch):
    monkeypatch.setattr("auto_router.security.get_settings",
                        lambda: SimpleNamespace(admin_token=ADMIN_TOKEN))
    from auto_router import main as main_module

    # No FastAPI lifespan; never run providers, background tasks or services.
    with TestClient(main_module.app, raise_server_exceptions=False) as client:
        yield client, main_module.app


def _example_path(route):
    import re
    return re.sub(r"\{[^}]+\}", "synthetic", route.path)


def test_all_registered_admin_paths_deny_anonymous_without_touching_handlers(fixture_client):
    client, app = fixture_client
    routes = [r for r in app.routes
              if isinstance(r, APIRoute) and r.path.startswith("/admin/")]
    assert len(routes) >= 12
    checked = set()
    for route in routes:
        for method in route.methods & {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            response = client.request(method, _example_path(route))
            assert response.status_code == 401, (
                f"unauthorized request reached handler {method} {route.path}: "
                f"{response.status_code}"
            )
            checked.add((method, route.path))
    assert ("POST", "/admin/circuits/reset") in checked
    assert ("GET", "/admin/providers") in checked


def test_known_admin_token_still_allows_fixture_only_provider_inventory(
    fixture_client, monkeypatch,
):
    client, app = fixture_client
    from auto_router import main as main_module
    original = main_module.state.providers
    main_module.state.providers = SimpleNamespace(
        providers=[SimpleNamespace(model_dump=lambda:{"name":"synthetic-only"})]
    )
    try:
        assert client.get("/admin/providers").status_code == 401
        good = client.get("/admin/providers",
                          headers={"X-Admin-Token": ADMIN_TOKEN})
        assert good.status_code == 200
        assert good.json() == {"providers":[{"name":"synthetic-only"}]}
    finally:
        main_module.state.providers = original


def test_unconfigured_token_denies_every_registered_admin_route(
    fixture_client, monkeypatch,
):
    client, app = fixture_client
    monkeypatch.setattr("auto_router.security.get_settings",
                        lambda: SimpleNamespace(admin_token=""))
    routes = [r for r in app.routes
              if isinstance(r, APIRoute) and r.path.startswith("/admin/")]
    assert len(routes) >= 12
    for route in routes:
        for method in route.methods & {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            response=client.request(method,_example_path(route))
            assert response.status_code == 401, (method,route.path,response.status_code)
