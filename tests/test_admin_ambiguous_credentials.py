"""Fail-closed admin auth when HTTP ingress sends multiple credentials.

All requests are TestClient no-lifespan local synthetic GETs. No real keys,
mutating admin routes, actual network ingress or external services.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from auto_router.security import require_admin

TOKEN = "synthetic-operator-admin-F8q7J2m5R9v3X6p1T4s0B8c2N5"


@pytest.fixture
def client(monkeypatch):
    for key in (
        "AUTO_ROUTER_INTERNAL_SERVICE_TOKEN",
        "AUTO_ROUTER_ASSISTX_EXECUTOR_SERVICE_TOKEN",
        "AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS",
        "AUTO_ROUTER_ROUTE_DECISION_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        "auto_router.security.get_settings",
        lambda: SimpleNamespace(admin_token=TOKEN),
    )
    app = FastAPI()

    @app.get("/admin/fixture", dependencies=[Depends(require_admin)])
    def read_only():
        return {"verified": True}

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.parametrize("header_pairs", [
    [("X-Admin-Token", TOKEN), ("Authorization", "Bearer wrong-credential")],
    [("X-Admin-Token", TOKEN), ("Authorization", "Bearer " + TOKEN)],
    [("X-Admin-Token", TOKEN), ("Authorization", "Invalid ignored")],
    [("X-Admin-Token", TOKEN), ("X-Admin-Token", "wrong-credential")],
    [("X-Admin-Token", "wrong-credential"), ("X-Admin-Token", TOKEN)],
    [("Authorization", "Bearer " + TOKEN), ("Authorization", "Bearer bad")],
    [("Authorization", "Bearer bad"), ("Authorization", "Bearer " + TOKEN)],
])
def test_multiple_or_conflicting_auth_headers_denied(client, header_pairs):
    response = client.get("/admin/fixture", headers=header_pairs)
    assert response.status_code == 401
    assert TOKEN not in response.text


def test_one_valid_admin_header_still_works(client):
    assert client.get(
        "/admin/fixture", headers={"X-Admin-Token": TOKEN},
    ).status_code == 200
    assert client.get(
        "/admin/fixture", headers={"Authorization": "Bearer " + TOKEN},
    ).status_code == 200
    assert client.get("/admin/fixture", auth=("admin", TOKEN)).status_code == 200


def test_unsupported_authorization_cannot_be_ignored(client):
    assert client.get(
        "/admin/fixture",
        headers={"Authorization": "Negotiate other"},
    ).status_code == 401
    assert client.get("/admin/fixture").status_code == 401
