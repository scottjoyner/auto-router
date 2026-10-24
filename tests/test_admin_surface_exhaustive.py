"""No-network tests for full administrative route census and synthetic auth checks."""
from __future__ import annotations

import json

import pytest

from scripts import verify_admin_surface_exhaustive as c


def openapi(*, remove=None, append=None, extra_method=None):
    paths = {}
    for method, path in c.EXPECTED:
        paths.setdefault(path, {})[method.lower()] = {"responses": {}}
    if remove:
        method, path = remove
        del paths[path][method.lower()]
        if not paths[path]:
            del paths[path]
    if append:
        method, path = append
        paths.setdefault(path, {})[method.lower()] = {"responses": {}}
    if extra_method:
        paths["/admin/providers"][extra_method] = {}
    return json.dumps({"openapi": "3.0.0", "paths": paths}).encode()


def ci(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("AUTO_ROUTER_CANARY_ENV", "disposable-github-ci")
    monkeypatch.setenv("AUTO_ROUTER_CANARY_SYNTHETIC_ADMIN_TOKEN", "smoke-admin-token")


def test_expected_source_count_and_method_inventory():
    assert len(c.EXPECTED) == 20
    assert sum(method == "GET" for method, _ in c.EXPECTED) == 16
    assert sum(method == "POST" for method, _ in c.EXPECTED) == 4
    assert ("POST", "/admin/circuits/reset/{owner}") in c.EXPECTED


def test_matching_openapi_surface_passes():
    assert c.admitted_operations(openapi()) == c.EXPECTED


@pytest.mark.parametrize("removed", [
    ("GET", "/admin/context/graph"),
    ("POST", "/admin/runtime-projection"),
    ("POST", "/admin/circuits/reset/{owner}"),
])
def test_missing_route_or_method_denies(removed):
    with pytest.raises(c.AdminSurfaceDenied, match="differ"):
        c.admitted_operations(openapi(remove=removed))


@pytest.mark.parametrize("new", [
    ("GET", "/admin/forgotten-inherited"),
    ("POST", "/admin/provider-secret-reset"),
])
def test_extra_route_or_method_denies(new):
    with pytest.raises(c.AdminSurfaceDenied, match="differ"):
        c.admitted_operations(openapi(append=new))


def test_extra_unapproved_http_method_denies():
    with pytest.raises(c.AdminSurfaceDenied, match="differ"):
        c.admitted_operations(openapi(extra_method="delete"))


def test_missing_ci_identity_denies_before_request(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("AUTO_ROUTER_CANARY_ENV", raising=False)
    with pytest.raises(c.AdminSurfaceDenied, match="disposable GitHub CI"):
        c.main()
    with pytest.raises(c.AdminSurfaceDenied):
        c.request_status("POST", "/admin/load")


def test_no_unapproved_endpoint_even_in_ci(monkeypatch):
    ci(monkeypatch)
    with pytest.raises(c.AdminSurfaceDenied, match="not approved"):
        c.request_status("POST", "/admin/backlog/burn-down")
    with pytest.raises(c.AdminSurfaceDenied, match="not approved"):
        c.request_status("GET", "/admin/load")
    with pytest.raises(c.AdminSurfaceDenied, match="refusing administrative response"):
        c.request_status("GET", "/admin/providers", read_schema=True)


def test_full_20_operations_no_network(monkeypatch, capsys):
    ci(monkeypatch)
    seen = []
    def fake(method, path, token=None, *, read_schema=False):
        seen.append((method, path, token, read_schema))
        if path == "/openapi.json":
            return 200, openapi()
        return 401, b""
    monkeypatch.setattr(c, "request_status", fake)
    c.main()
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == "DISPOSABLE_ALL_ADMIN_AUTH_NEGATIVES_PASS"
    assert report["get_operations"] == 16
    assert report["post_operations"] == 4
    assert report["mounted_admin_operations"] == 20
    assert report["authenticated_mutation_attempts"] == 0
    assert len(seen) == 41  # OpenAPI GET plus 2 denials for all 20 operations
    assert sum(m == "POST" for m, _, _, _ in seen) == 8
    assert all(token in (None, c.WRONG) for _, _, token, _ in seen)


@pytest.mark.parametrize("bypassed", [
    ("GET", "/admin/providers"),
    ("POST", "/admin/load"),
    ("POST", "/admin/runtime-projection"),
])
def test_any_unprotected_operation_denies(monkeypatch, bypassed):
    ci(monkeypatch)
    def fake(method, path, token=None, *, read_schema=False):
        if path == "/openapi.json":
            return 200, openapi()
        if (method, path) == bypassed:
            return 200, b""
        return 401, b""
    monkeypatch.setattr(c, "request_status", fake)
    with pytest.raises(c.AdminSurfaceDenied, match="authentication bypass"):
        c.main()
