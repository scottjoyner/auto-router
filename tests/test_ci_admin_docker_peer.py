"""Socketless contract for separate Docker-network admin status verifier."""
from __future__ import annotations

import json

import pytest
from scripts import ci_admin_docker_peer as canary


class Response:
    def __init__(self, code):
        self.status = code

    def read(self, *_args, **_kw):
        raise AssertionError("must not read body")


class Connector:
    called = []

    def __init__(self, host, port, timeout):
        assert (host, port, timeout) == ("llm-router", 8088, 3)

    def request(self, method, path, headers):
        assert method == "GET" and path in ("/health", *canary.SAFE_PATHS)
        assert "Authorization" not in headers and "X-Admin-Token" not in headers
        self.path = path
        self.called.append(path)

    def getresponse(self):
        return Response(200 if self.path == "/health" else 401)

    def close(self):
        pass


def test_real_http_status_probe_has_fixed_service_identity_and_no_secrets():
    Connector.called = []
    expected = lambda path: canary._status(path, connector=Connector)
    receipt = canary.verify(status=expected)
    assert receipt["checks_passed"]
    assert receipt["production_admission"] == "HOLD"
    assert receipt["response_bodies_read"] is False
    assert Connector.called == ["/health", *canary.SAFE_PATHS]
    assert "token" not in json.dumps(receipt).lower()


@pytest.mark.parametrize("health,admin", [
    (401, 401), (404, 401), (None, 401), (200, 200),
    (200, 403), (200, None),
])
def test_wrong_service_or_admin_not_denied_fails(health, admin):
    result = canary.verify(status=lambda path: health if path == "/health" else admin)
    assert not result["checks_passed"]
    assert result["production_admission"] == "HOLD"


def test_no_runtime_ci_optin_returns_hold_without_network(monkeypatch):
    monkeypatch.delenv("AUTO_ROUTER_CI_DOCKER_PEER", raising=False)
    monkeypatch.setattr(
        canary, "verify", lambda **kw: pytest.fail("no network permitted"),
    )
    assert canary.main() == 2


def test_only_ci_context_runs(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_CI_DOCKER_PEER", "true")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(canary, "verify", lambda: {"checks_passed": True})
    assert canary.main() == 0
