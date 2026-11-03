"""Synthetic cross-repo contract: exact AssistX catalog client and router ASGI.

No external HTTP, lifespan, provider probe, model load or Task dispatch.
ASSISTX_CLIENT_EXACT must point to SHA-pinned separate source checkout.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
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
