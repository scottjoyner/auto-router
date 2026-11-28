from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_router.ops_dashboard_routes import register_ops_dashboard_routes


TOKEN = "test-admin-token-abcdef123456"


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(
        "auto_router.security.get_settings",
        lambda: SimpleNamespace(admin_token=TOKEN),
    )
    app = FastAPI()
    register_ops_dashboard_routes(app, SimpleNamespace())
    return TestClient(app, raise_server_exceptions=False)


def test_ops_admin_routes_reject_anonymous(monkeypatch) -> None:
    client = _client(monkeypatch)
    assert client.get("/admin/ops/summary").status_code == 401
    assert client.get("/admin/ops/preflight").status_code == 401


def test_ops_admin_routes_accept_admin_token(monkeypatch) -> None:
    client = _client(monkeypatch)
    for path in ("/admin/ops/summary", "/admin/ops/preflight"):
        response = client.get(path, headers={"X-Admin-Token": TOKEN})
        assert response.status_code not in (401, 403)


def test_ops_metrics_remains_ungated(monkeypatch) -> None:
    client = _client(monkeypatch)
    assert client.get("/metrics/ops").status_code != 401
