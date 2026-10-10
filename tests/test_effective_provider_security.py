"""Regression contract for secret-free, authenticated runtime provider diagnostics."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from auto_router import main
from auto_router.config import ProviderRegistry
from auto_router.effective_provider_snapshot import (
    effective_snapshot,
    endpoint_reference,
)
from auto_router.models import ModelConfig, ProviderConfig

TOKEN = "synthetic-admin-only-not-production-token"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        "auto_router.security.get_settings",
        lambda: SimpleNamespace(admin_token=TOKEN),
    )
    provider = ProviderConfig(
        name="lmstudio-destroyer-k2",
        id="candidate-001",
        type="lmstudio",
        node_id="destroyer",
        base_url="http://test-user:PRIVATE_PASSWORD@destroyer:1235/v1?token=LEAK",
        access_urls=[
            "https://user:ACCESS_SECRET@destroyer.local:8443/v1?bearer=LEAK",
        ],
        headers={"Authorization": "Bearer SHOULD_NOT_LEAK"},
        api_key_env="PRIVATE_KEY_ENV_DO_NOT_LEAK",
        models=[ModelConfig(alias="expected-k2-36b", provider_model="K2 Horizon 36B")],
        enabled=True,
    )
    monkeypatch.setattr(
        main.state, "providers", ProviderRegistry(providers=[provider]), raising=False
    )
    monkeypatch.setattr(
        main.state,
        "runtime_projection_manager",
        SimpleNamespace(
            current=SimpleNamespace(
                generation=5,
                revision="synthetic-projection",
                expires_at_ms=9999999999999,
            )
        ),
        raising=False,
    )
    # Do not enter the ASGI lifespan: that loads live provider config and
    # starts background tasks, destroying this isolated synthetic fixture.
    test = TestClient(main.app, raise_server_exceptions=False)
    try:
        yield test
    finally:
        test.close()


GATED = [
    ("GET", "/admin/providers"),
    ("GET", "/admin/providers/effective"),
    ("GET", "/admin/providers/health"),
    ("GET", "/admin/context"),
    ("GET", "/admin/context/graph"),
    ("GET", "/admin/placement"),
    ("GET", "/admin/agent-workers"),
    ("GET", "/admin/usage"),
    ("GET", "/admin/circuits"),
    ("GET", "/admin/agent-jobs"),
    ("POST", "/admin/load"),
    ("POST", "/admin/circuits/reset"),
    ("POST", "/admin/circuits/reset/candidate"),
]


@pytest.mark.parametrize("method,path", GATED)
def test_inherited_admin_route_rejects_anonymous(client, method, path):
    assert client.request(method, path).status_code == 401


@pytest.mark.parametrize("method,path", GATED)
def test_inherited_admin_route_rejects_wrong_token(client, method, path):
    assert client.request(method, path, headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_sanitized_runtime_snapshot_only_after_auth(client):
    response = client.get(
        "/admin/providers/effective",
        headers={"X-Admin-Token": TOKEN},
    )
    assert response.status_code == 200
    raw = response.text
    for forbidden in (
        "PRIVATE_PASSWORD",
        "ACCESS_SECRET",
        "Bearer",
        "LEAK",
        "PRIVATE_KEY_ENV_DO_NOT_LEAK",
    ):
        assert forbidden not in raw
    body = response.json()
    assert body["kind"] == "EFFECTIVE_ROUTER_MEMORY_READ_ONLY"
    assert body["state_source"] == "LIVE_ASGI_STATE_NOT_BOOTSTRAP_PROVIDER_FILE"
    assert body["provider_count"] == 1
    assert body["enabled_count"] == 1
    record = body["providers"][0]
    assert record["enabled_in_router_memory"] is True
    assert record["name"] == "lmstudio-destroyer-k2"
    assert record["effective_base_url"]["hostname"] == "destroyer"
    assert record["effective_base_url"]["port"] == 1235
    assert record["effective_base_url"]["userinfo_or_query_present"] is True
    assert record["configured_models"][0]["provider_model"] == "K2 Horizon 36B"
    assert record["resident_model_identity_verified"] is False
    assert record["service_reachability_verified"] is False
    assert record["admission_granted"] is False
    assert body["may_modify_routes"] is False
    assert body["may_prove_model_loaded"] is False


def test_provider_snapshot_is_stable_given_same_captured_time(client):
    one = effective_snapshot(main.state, observed_at_ms=1800000000000)
    two = effective_snapshot(main.state, observed_at_ms=1800000000000)
    assert one == two
    assert len(one["snapshot_sha256"]) == 64
    assert one["projection"]["state"] == ("PROJECTION_CLAIM_CURRENT_AT_CAPTURE_NOT_PROVIDER_HEALTH")


def test_non_probed_origin_and_invalid_endpoint_remain_explicit():
    for src in ("file:///root/private", "http:///123", "malformed", None):
        assert endpoint_reference(src)["status"] == "INVALID"
    parsed = endpoint_reference("https://user:secret@host.example:443/v1?api=SECRET")
    assert parsed["hostname"] == "host.example"
    assert parsed["port"] == 443
    assert "secret" not in json.dumps(parsed)
    assert parsed["userinfo_or_query_present"] is True


def test_invalid_registry_never_becomes_empty_healthy_snapshot():
    with pytest.raises(ValueError):
        effective_snapshot(SimpleNamespace(), observed_at_ms=1800000000000)
    with pytest.raises(ValueError):
        effective_snapshot(
            SimpleNamespace(providers=SimpleNamespace(providers=[None] * 1001)),
            observed_at_ms=1800000000000,
        )


def test_admin_unconfigured_fails_closed(client, monkeypatch):
    monkeypatch.setattr(
        "auto_router.security.get_settings",
        lambda: SimpleNamespace(admin_token=""),
    )
    assert client.get("/admin/providers/effective").status_code == 401
    assert client.get("/admin/providers").status_code == 401
