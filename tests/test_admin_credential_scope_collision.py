"""Synthetic no-lifespan proof: service credentials cannot become admin keys.

An otherwise matching X-Admin-Token, Basic or Bearer must never authorize
/admin when the configured admin token is also a router internal-service,
executor-service, callback or route-decision token. No real credentials,
external networking, or mutating admin handlers are involved.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from auto_router.security import require_admin, _admin_credential_is_separate

ADMIN = "synthetic-admin-token-A8fQ5vK7yR4cM9hJ2wL6sP3eT1"
SCOPES = (
    "AUTO_ROUTER_INTERNAL_SERVICE_TOKEN",
    "AUTO_ROUTER_ASSISTX_EXECUTOR_SERVICE_TOKEN",
    "AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS",
    "AUTO_ROUTER_ROUTE_DECISION_TOKEN",
)


@pytest.fixture
def isolated_admin(monkeypatch):
    for key in SCOPES:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        "auto_router.security.get_settings",
        lambda: SimpleNamespace(admin_token=ADMIN),
    )
    app = FastAPI()

    @app.get("/admin/fixture", dependencies=[Depends(require_admin)])
    async def fixture():
        return {"safe": True}

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_distinct_admin_key_still_authorizes_only_fixture_get(isolated_admin):
    client = isolated_admin
    assert _admin_credential_is_separate(ADMIN)
    assert client.get("/admin/fixture").status_code == 401
    response = client.get(
        "/admin/fixture", headers={"X-Admin-Token": ADMIN},
    )
    assert response.status_code == 200
    assert response.json() == {"safe": True}


@pytest.mark.parametrize("scope", SCOPES)
def test_service_and_admin_key_collision_denies_every_auth_mechanism(
    isolated_admin, monkeypatch, scope,
):
    client = isolated_admin
    monkeypatch.setenv(scope, ADMIN)
    assert not _admin_credential_is_separate(ADMIN)
    for headers in (
        {"X-Admin-Token": ADMIN},
        {"Authorization": f"Bearer {ADMIN}"},
    ):
        response = client.get("/admin/fixture", headers=headers)
        assert response.status_code == 401
        assert ADMIN not in response.text
    response = client.get("/admin/fixture", auth=("admin", ADMIN))
    assert response.status_code == 401
    assert ADMIN not in response.text


@pytest.mark.parametrize("scope", SCOPES)
def test_distinct_other_service_key_does_not_block_admin(
    isolated_admin, monkeypatch, scope,
):
    client = isolated_admin
    monkeypatch.setenv(scope, "independent-service-synthetic-H7gZ9vJ3nM2xL8a5")
    assert _admin_credential_is_separate(ADMIN)
    assert client.get(
        "/admin/fixture", headers={"X-Admin-Token": ADMIN},
    ).status_code == 200


def test_empty_admin_and_unconfigured_service_keys_fail_closed(
    isolated_admin, monkeypatch,
):
    client = isolated_admin
    assert not _admin_credential_is_separate("")
    monkeypatch.setattr(
        "auto_router.security.get_settings",
        lambda: SimpleNamespace(admin_token=""),
    )
    assert client.get("/admin/fixture").status_code == 401
    assert client.get(
        "/admin/fixture", headers={"X-Admin-Token": ADMIN},
    ).status_code == 401
