"""No-token / wrong-token requests cannot execute optional admin handlers.

The four optional route registrars are assembled with synthetic data-only
state and no ASGI lifespan, model calls, outbox writes or production services.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_router import security
from auto_router.backlog_routes import register_backlog_routes
from auto_router.cli_routes import register_cli_routes
from auto_router.live_model_routes import register_live_model_routes
from auto_router.service_routes import register_service_routes


def _app():
    app = FastAPI()
    state = SimpleNamespace(
        cli_discovery=[],
        event_outbox=object(),
        live_models=object(),
        model_registry=object(),
        service_status=object(),
        service_store=object(),
    )
    register_backlog_routes(app, state)
    register_cli_routes(app, state)
    register_live_model_routes(app, state)
    register_service_routes(app, state)
    return app


ROUTES = [
    ("POST", "/admin/backlog/dry-run"),
    ("POST", "/admin/backlog/burn-down"),
    ("GET", "/admin/backlog/assistx/config"),
    ("GET", "/admin/agent-clis"),
    ("POST", "/admin/agent-clis/discover"),
    ("GET", "/admin/live-models"),
    ("POST", "/admin/live-models/refresh"),
    ("POST", "/admin/providers/probe"),
    ("GET", "/admin/services"),
    ("GET", "/admin/outbox"),
    ("POST", "/admin/outbox/synthetic-event-id/delivered"),
    ("POST", "/admin/outbox/synthetic-event-id/failed"),
]


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize("credential", [None, "incorrect-synthetic-token"])
def test_no_or_wrong_admin_token_denied_before_handler(monkeypatch, method, path, credential):
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(admin_token="expected-synthetic-token"),
    )
    headers = {} if credential is None else {"X-Admin-Token": credential}
    with TestClient(_app()) as client:
        response = client.request(
            method, path, headers=headers, json={} if method == "POST" else None
        )
    assert response.status_code == 401


def test_synthetic_authorized_read_only_cli_catalog(monkeypatch):
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(admin_token="expected-synthetic-token"),
    )
    with TestClient(_app()) as client:
        response = client.get(
            "/admin/agent-clis",
            headers={"X-Admin-Token": "expected-synthetic-token"},
        )
    assert response.status_code == 200
    assert response.json() == {"clis": []}
