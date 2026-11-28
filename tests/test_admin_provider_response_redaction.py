"""Read-only synthetic guard for the authenticated legacy provider inventory.

The operator endpoint must retain useful model names while never serializing
credential files, header values, raw provider URLs or arbitrary future fields.
No live providers, credentials, sockets or fleet processes are used.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from auto_router import main as router_main

TOKEN = "synthetic-admin-secret"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        "auto_router.security.get_settings",
        lambda: SimpleNamespace(admin_token=TOKEN),
    )
    raw = {
        "id": "fixture-provider",
        "name": "fixture",
        "type": "openai_compatible",
        "enabled": True,
        "priority": 1,
        "models": [{
            "alias": "synthetic-model",
            "provider_model": "synthetic/id",
            "context_window": 2048,
            "task_family_scores": {"private": "SECRET_NESTED"},
            "quota": {"unexpected": "SECRET_QUOTA"},
            "future_model_token": "SECRET_FUTURE_MODEL",
        }],
        "headers": {
            "Authorization": "Bearer SECRET_HEADER",
            "X-Admin-Token": "SECRET_SECONDARY",
        },
        "api_key_env": "SECRET_ENV_VARIABLE",
        "api_key_file": "/private/SECRET_KEY_FILE",
        "base_url": "https://SECRET_USER:SECRET_PASS@host.invalid/v1?token=SECRET_QUERY",
        "access_urls": ["https://host.invalid/?api_key=SECRET_ACCESS"],
        "unexpected_future_secret": "SECRET_FUTURE_PROVIDER",
    }
    fake = SimpleNamespace(model_dump=lambda: raw)
    # AppState initializes provider data during lifespan, which these
    # socketless tests intentionally never invoke. Monkeypatch must create
    # the optional member and automatically restore its absence afterward.
    monkeypatch.setattr(
        router_main.state, "providers", SimpleNamespace(providers=[fake]),
        raising=False,
    )
    yield TestClient(router_main.app, raise_server_exceptions=False), raw


def test_operator_inventory_is_strict_allowlist(client):
    http, raw = client
    response = http.get("/admin/providers", headers={"X-Admin-Token": TOKEN})
    assert response.status_code == 200
    payload = response.json()
    assert payload == {"providers": [{
        "id": "fixture-provider",
        "name": "fixture",
        "type": "openai_compatible",
        "enabled": True,
        "priority": 1,
        "models": [{
            "alias": "synthetic-model",
            "provider_model": "synthetic/id",
            "context_window": 2048,
        }]
    }]}
    serialized = json.dumps(payload)
    assert "SECRET_" not in serialized
    # The API view must not destructively strip secrets from provider state.
    assert raw["headers"]["Authorization"] == "Bearer SECRET_HEADER"


def test_denied_caller_receives_no_inventory(client):
    http, _ = client
    for headers in ({}, {"X-Admin-Token": "wrong-synthetic"}):
        response = http.get("/admin/providers", headers=headers)
        assert response.status_code == 401
        assert "providers" not in response.json()


def test_future_provider_fields_and_nested_model_metadata_not_serialized():
    raw = {
        "name": "only-name",
        "models": [{
            "alias": "model-a",
            "credentials": "SECRET_NEW_FIELD",
        }],
        "future": "SECRET_TOP_LEVEL",
    }
    provider = SimpleNamespace(model_dump=lambda: raw)
    assert router_main._redacted_admin_provider(provider) == {
        "name": "only-name", "models": [{"alias": "model-a"}]
    }


def test_invalid_non_mapping_provider_is_rejected_without_raw_serialization():
    with pytest.raises(ValueError):
        router_main._redacted_admin_provider(
            SimpleNamespace(model_dump=lambda: ["SECRET_BAD_SHAPE"])
        )
