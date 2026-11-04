"""Synthetic cross-repo contract: exact AssistX catalog client and router ASGI.

No external HTTP, lifespan, provider probe, model load or Task dispatch.
ASSISTX_CLIENT_EXACT must point to SHA-pinned separate source checkout.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from auto_router import main, security
from auto_router.config import ProviderRegistry
from auto_router.models import ModelConfig, ProviderConfig

EXPECTED = "b822fe337770dafc17ce04145627bdc6490f2876172af00fe7808f179e3c02c2"
TOKEN = "synthetic-admin-only-not-real"


@pytest.fixture
def environment(monkeypatch):
    raw = os.getenv("ASSISTX_CLIENT_EXACT")
    if not raw:
        pytest.skip("dedicated workflow supplies a pinned AssistX source checkout")
    path = Path(raw)
    assert path.is_file()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED
    spec = importlib.util.spec_from_file_location("exact_assistx_client", path)
    assert spec and spec.loader
    catalog = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(catalog)
    monkeypatch.setattr(security, "get_settings", lambda: SimpleNamespace(admin_token=TOKEN))
    provider = ProviderConfig(
        id="stage-1", name="synthetic", type="lmstudio", node_id="DESTROYER",
        base_url="http://username:private_fixture@100.64.44.77:1234/v1",
        headers={"Authorization": "PRIVATE_FIXTURE_HEADER"},
        models=[ModelConfig(alias="ornith", provider_model="Ornith-1.5")],
        enabled=True,
    )
    monkeypatch.setattr(main.state, "providers", ProviderRegistry(providers=[provider]), raising=False)
    monkeypatch.setattr(
        main.state, "runtime_projection_manager",
        SimpleNamespace(current=SimpleNamespace(
            generation=17, revision="synthetic", expires_at_ms=9999999999999,
        )), raising=False,
    )
    # Do not enter TestClient context: that launches ASGI lifespan, which
    # reads provider files and starts background infrastructure work.
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        yield catalog, client
    finally:
        client.close()


def test_real_asgi_auth_and_matching_client_parse(environment):
    parser, router = environment
    assert router.get("/admin/providers/effective").status_code == 401
    assert router.get("/admin/providers/effective", headers={"X-Admin-Token": "wrong"}).status_code == 401
    r = router.get("/admin/providers/effective", headers={"X-Admin-Token": TOKEN})
    assert r.status_code == 200
    assert "private_fixture" not in r.text and "PRIVATE_FIXTURE_HEADER" not in r.text
    assert r.json()["may_prove_model_loaded"] is False
    calls = []

    def fetch(url, path, *, admin_token):
        route = urlsplit(url).path
        calls.append((route, admin_token))
        result = router.get(route, headers={"X-Admin-Token": admin_token})
        if result.status_code >= 400:
            raise HTTPError(url, result.status_code, "denied", {}, None)
        return result.json(), 0.1

    result = parser.fetch_provider_inventory(
        base_url="http://router.example:8088", override_url="",
        admin_token=TOKEN, fetch_json=fetch,
    )
    assert result["destroyer"] == ["Ornith-1.5"]
    assert result["stage-1"] == ["Ornith-1.5"]
    assert result["100.64.44.77"] == ["Ornith-1.5"]
    assert calls == [("/admin/providers/effective", TOKEN)]


def test_no_unauthorized_client_fallback(environment):
    parser, router = environment
    calls = []

    def fetch(url, path, *, admin_token):
        calls.append(urlsplit(url).path)
        response = router.get(urlsplit(url).path, headers={"X-Admin-Token": admin_token})
        raise HTTPError(url, response.status_code, "denied", {}, None)

    assert parser.fetch_provider_inventory(
        base_url="http://router.example:8088", override_url="",
        admin_token="wrong", fetch_json=fetch,
    ) == {}
    assert calls == ["/admin/providers/effective"]


def test_legacy_404_fallback_is_authenticated_and_only_on_exact_404(environment):
    parser, router = environment
    paths = []

    def fetch(url, path, *, admin_token):
        route = urlsplit(url).path
        paths.append((route, admin_token))
        if route.endswith("/effective"):
            raise HTTPError(url, 404, "synthetic old router", {}, None)
        response = router.get(route, headers={"X-Admin-Token": admin_token})
        if response.status_code >= 400:
            raise HTTPError(url, response.status_code, "denied", {}, None)
        return response.json(), 0.1

    parsed = parser.fetch_provider_inventory(
        base_url="http://router.example:8088",
        override_url="",
        admin_token=TOKEN,
        fetch_json=fetch,
    )
    assert parsed["destroyer"] == ["Ornith-1.5"]
    assert paths == [
        ("/admin/providers/effective", TOKEN),
        ("/admin/providers", TOKEN),
    ]



def test_exact_live_entrypoint_all_admin_routes_require_admin_without_lifespan(monkeypatch):
    """The deployed main_live entrypoint mounts routes beyond main.app.

    Import this last: main_live registers its routes on the shared app. This
    test remains synthetic, uses no lifespan and opens no network connections.
    """
    from fastapi.routing import APIRoute

    from auto_router import offline_guard

    # Hosted CI has no real provider config and must never read a live one.
    # Stub ONLY the pre-import config-file guard for this route-graph audit;
    # the strict-offline toggle and all actual admin deps stay unchanged.
    monkeypatch.setattr(
        offline_guard, "enforce_strict_offline_provider_config", lambda: None
    )
    from auto_router import main_live

    admin_routes = [
        r for r in main_live.app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/admin/")
    ]
    assert len(admin_routes) >= 20
    seen = set()
    for route in admin_routes:
        for method in route.methods or []:
            seen.add((method, route.path))
        deps = {d.call for d in route.dependant.dependencies}
        assert security.require_admin in deps, (
            f"missing shared admin authorization: {route.path}"
        )
    assert ("GET", "/admin/providers") in seen
    assert ("GET", "/admin/providers/effective") in seen
    assert ("GET", "/admin/admission") in seen
    assert ("POST", "/admin/runtime-projection") in seen


def _load_exact_current_main_controlroom(monkeypatch, catalog):
    """Load the exact PR #546 Control Room without executing AssistX __init__."""
    raw = os.getenv("ASSISTX_CONTROL_EXACT")
    assert raw, "hosted workflow must supply exact current-main Control Room"
    source = Path(raw)
    assert source.is_file()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        "bee6ed102474d3544e90c460eb6dfe3e9cbd6d3b8e6824877b0faa4b84becd77"
    )
    pkg = ModuleType("assistx")
    pkg.__path__ = [str(source.parent)]
    monkeypatch.setitem(sys.modules, "assistx", pkg)
    monkeypatch.setitem(sys.modules, "assistx.router_provider_inventory", catalog)
    # Only unrelated Control Room ancillary dependencies are stubbed.
    # The actual router inventory and exact Control Room code stay untouched.
    recovery = ModuleType("assistx.control_room_recovery")
    recovery.island_recovery_snapshot = lambda *_args, **_kwargs: {}
    deps = ModuleType("assistx.deps")
    deps.load_redis_module = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, recovery.__name__, recovery)
    monkeypatch.setitem(sys.modules, deps.__name__, deps)
    spec = importlib.util.spec_from_file_location("assistx.control_room", source)
    assert spec and spec.loader
    control = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, control)
    spec.loader.exec_module(control)
    return control


def test_exact_current_main_control_room_uses_real_sanitized_asgi(environment, monkeypatch):
    parser, router = environment
    control = _load_exact_current_main_controlroom(monkeypatch, parser)
    monkeypatch.setenv("AUTO_ROUTER_BASE_URL", "http://synthetic-router.example:8088")
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", TOKEN)
    monkeypatch.delenv("AUTO_ROUTER_ADMIN_PROVIDERS_URL", raising=False)
    calls = []

    def inprocess_fetch(url, path, *, admin_token):
        route = urlsplit(url).path
        calls.append((route, admin_token))
        result = router.get(route, headers={"X-Admin-Token": admin_token})
        if result.status_code >= 400:
            raise HTTPError(url, result.status_code, "synthetic denial", {}, None)
        return result.json(), 0.1

    monkeypatch.setattr(control, "_http_json", inprocess_fetch)
    result = control._provider_catalog()
    assert result["destroyer"] == ["Ornith-1.5"]
    assert result["stage-1"] == ["Ornith-1.5"]
    assert calls == [("/admin/providers/effective", TOKEN)]
    # A nonmatching token must not trigger old-router fallback or reveal nodes.
    calls.clear()
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "wrong")
    assert control._provider_catalog() == {}
    assert calls == [("/admin/providers/effective", "wrong")]


def test_exact_current_main_control_room_legacy_404_fallback(environment, monkeypatch):
    parser, router = environment
    control = _load_exact_current_main_controlroom(monkeypatch, parser)
    monkeypatch.setenv("AUTO_ROUTER_BASE_URL", "http://synthetic-router.example:8088")
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", TOKEN)
    monkeypatch.delenv("AUTO_ROUTER_ADMIN_PROVIDERS_URL", raising=False)
    calls = []

    def inprocess_fetch(url, path, *, admin_token):
        route = urlsplit(url).path
        calls.append((route, admin_token))
        if route == "/admin/providers/effective":
            raise HTTPError(url, 404, "synthetic prepatch router", {}, None)
        result = router.get(route, headers={"X-Admin-Token": admin_token})
        if result.status_code >= 400:
            raise HTTPError(url, result.status_code, "synthetic denial", {}, None)
        return result.json(), 0.1

    monkeypatch.setattr(control, "_http_json", inprocess_fetch)
    result = control._provider_catalog()
    assert result["destroyer"] == ["Ornith-1.5"]
    assert calls == [
        ("/admin/providers/effective", TOKEN),
        ("/admin/providers", TOKEN),
    ]
