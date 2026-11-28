"""Synthetic local read-only auth contract for GET /admin/providers."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from auto_router import main as main_module

TOKEN="research-fixture-admin-token-never-production"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        "auto_router.security.get_settings",
        lambda: SimpleNamespace(admin_token=TOKEN),
    )
    fake=SimpleNamespace(
        providers=[SimpleNamespace(model_dump=lambda:{"name":"fixture-only","models":["synthetic"]})]
    )
    monkeypatch.setattr(main_module.state, "providers", fake, raising=False)
    # Deliberately no lifespan: never load actual providers or start services.
    c=TestClient(main_module.app, raise_server_exceptions=False, follow_redirects=False)
    try:
        yield c
    finally:
        c.close()


def test_anonymous_provider_listing_denied(client):
    response=client.get("/admin/providers")
    assert response.status_code==401
    assert "providers" not in response.json()


def test_wrong_token_provider_listing_denied(client):
    assert client.get("/admin/providers",headers={"X-Admin-Token":"incorrect"}).status_code==401


def test_correct_fixture_token_reaches_provider_listing(client):
    response=client.get("/admin/providers",headers={"X-Admin-Token":TOKEN})
    assert response.status_code==200
    assert response.json()=={"providers":[{"name":"fixture-only","models":["synthetic"]}]}


def test_no_token_configuration_stays_closed(client,monkeypatch):
    monkeypatch.setattr(
        "auto_router.security.get_settings",
        lambda: SimpleNamespace(admin_token=""),
    )
    assert client.get("/admin/providers").status_code==401


def test_basic_auth_succeeds_on_fixture_token(client):
    response=client.get("/admin/providers",auth=("admin",TOKEN))
    assert response.status_code==200



def test_bearer_auth_succeeds_on_fixture_token(client):
    response=client.get("/admin/providers",headers={"Authorization":f"Bearer {TOKEN}"})
    assert response.status_code==200


def test_basic_wrong_user_with_valid_token_is_denied(client):
    # Correct token MUST NOT bypass the documented Basic username policy.
    response=client.get("/admin/providers",auth=("wrong-user",TOKEN))
    assert response.status_code==401


def test_bearer_wrong_token_denied(client):
    assert client.get(
        "/admin/providers",
        headers={"Authorization":"Bearer wrong-synthetic-token"}
    ).status_code == 401
