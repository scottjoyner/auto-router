"""Scoped credentials must authorize any persisted routing decision."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_router.assistx_routes import register_assistx_routes


@pytest.fixture
def route_client(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "synthetic-admin-cannot-decide")
    monkeypatch.setenv("AUTO_ROUTER_INTERNAL_SERVICE_TOKEN", "synthetic-executor-cannot-decide")
    app = FastAPI()
    # No meaningful state: unauthorized calls must terminate before touching it.
    register_assistx_routes(app, SimpleNamespace())
    return TestClient(app, raise_server_exceptions=True)


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong"},
    {"Authorization": "Basic dGVzdDp0ZXN0"},
    {"Authorization": "Bearer synthetic-admin-cannot-decide"},
    {"Authorization": "Bearer synthetic-executor-cannot-decide"},
    {"X-Route-Decision-Token": "synthetic-route-only"},
    {"Authorization": "Bearer"},
])
def test_route_decision_refuses_wrong_or_other_scope(route_client, monkeypatch, headers):
    monkeypatch.setenv("AUTO_ROUTER_ROUTE_DECISION_TOKEN", "synthetic-route-only")
    response = route_client.post(
        "/api/routes/request",
        json={"correlation_id": "not-admitted"},
        headers=headers,
    )
    assert response.status_code == 401
    assert "synthetic-route-only" not in response.text


def test_route_decision_has_no_unsafe_unconfigured_default(route_client, monkeypatch):
    monkeypatch.delenv("AUTO_ROUTER_ROUTE_DECISION_TOKEN", raising=False)
    response = route_client.post(
        "/api/routes/request",
        json={"correlation_id": "not-admitted"},
        headers={"Authorization": "Bearer synthetic-route-only"},
    )
    assert response.status_code == 401


def test_route_decision_auth_cannot_be_disabled_with_executor_toggle(route_client, monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_ROUTE_DECISION_TOKEN", "synthetic-route-only")
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "false")
    response = route_client.post("/api/routes/request", json={"correlation_id": "test"})
    assert response.status_code == 401
