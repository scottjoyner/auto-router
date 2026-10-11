"""Offline regressions for the *read-only* administrative cutover gate."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "verify_admin_cutover_readonly.py"
spec = importlib.util.spec_from_file_location("readonly_admin_gate", SOURCE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

TOKEN = "test-token-not-a-production-secret"


def _snapshot():
    return {
        "schema_version": 1,
        "kind": "EFFECTIVE_ROUTER_MEMORY_READ_ONLY",
        "state_source": "LIVE_ASGI_STATE_NOT_BOOTSTRAP_PROVIDER_FILE",
        "no_probes_or_dispatch": True,
        "may_modify_routes": False,
        "may_prove_model_loaded": False,
        "provider_count": 1,
        "providers": [
            {
                "provider_id": "synthetic-only",
                "enabled_in_router_memory": True,
                "effective_base_url": {"status": "PARSED_NOT_PROBED", "hostname": "local", "port": 1235},
                "effective_access_urls": [],
                "configured_models": [{"alias": "only-configuration", "provider_model": "synthetic"}],
                "resident_model_identity_verified": False,
                "service_reachability_verified": False,
                "admission_granted": False,
            }
        ],
    }


class FakeResponse:
    def __init__(self, code: int, body: bytes):
        self.status, self.body = code, body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, max_bytes: int) -> bytes:
        return self.body[:max_bytes]


class FakeOpener:
    def __init__(self, *, anonymous_status=401, auth_status=200, snapshot=None):
        self.calls = []
        self.anonymous_status = anonymous_status
        self.auth_status = auth_status
        self.snapshot = _snapshot() if snapshot is None else snapshot

    def open(self, request, timeout):
        self.calls.append((request.full_url, request.get_header("X-admin-token"), timeout))
        if request.get_header("X-admin-token"):
            return FakeResponse(self.auth_status, json.dumps(self.snapshot).encode())
        return FakeResponse(self.anonymous_status, b"PRIVATE_BODY_MUST_NOT_BE_READ")


@pytest.mark.parametrize("base", [
    "https://127.0.0.1:8088", "http://127.0.0.1:8089",
    "http://router.example:8088", "http://user:secret@localhost:8088",
    "http://127.0.0.1:8088/?admin=secret", "http://127.0.0.1:8088/not-local",
])
def test_only_local_fixed_transport_accepted(base):
    with pytest.raises(ValueError):
        gate.validate_loopback_base(base)


def test_anonymous_only_has_no_token_and_four_readonly_gets():
    transport = FakeOpener()
    result = gate.check("http://127.0.0.1:8088", token=TOKEN, opener=transport, anonymous_only=True)
    assert result == {"anonymous_admin_denied": True, "authenticated_snapshot": "NOT_CHECKED"}
    assert len(transport.calls) == 4
    assert all(token is None and timeout == 5 for _, token, timeout in transport.calls)


def test_hardened_anonymous_and_sanitized_auth_paths_pass():
    transport = FakeOpener()
    result = gate.check("http://127.0.0.1:8088", token=TOKEN, opener=transport)
    assert result == {"anonymous_admin_denied": True, "authenticated_snapshot": "PASS"}
    assert len(transport.calls) == 5
    assert transport.calls[-1][1] == TOKEN
    assert transport.calls[-1][0].endswith("/admin/providers/effective")
    assert all(token is None for _, token, _ in transport.calls[:-1])
    assert TOKEN not in json.dumps(result)
    assert "PRIVATE_BODY_MUST_NOT_BE_READ" not in json.dumps(result)


def test_anonymous_200_is_blocker_and_does_not_request_with_admin_token():
    transport = FakeOpener(anonymous_status=200)
    result = gate.check("http://127.0.0.1:8088", token=TOKEN, opener=transport)
    assert result["reason"] == "ANONYMOUS_ADMIN_NOT_DENIED"
    assert len(transport.calls) == 1
    assert all(token is None for _, token, _ in transport.calls)


def test_no_token_refuses_to_certify_authenticated_endpoint():
    transport = FakeOpener()
    result = gate.check("http://127.0.0.1:8088", token=None, opener=transport)
    assert result["reason"] == "APPROVED_ADMIN_TOKEN_REQUIRED"
    assert len(transport.calls) == 4


@pytest.mark.parametrize("bad", [
    {"schema_version": 2},
    {"schema_version": 1, "providers": []},
    {"schema_version": 1, "providers": [{"headers": {"Authorization": "SECRET"}}]},
])
def test_malformed_schema_is_denied(bad):
    transport = FakeOpener(snapshot=bad)
    result = gate.check("http://127.0.0.1:8088", token=TOKEN, opener=transport)
    assert result["authenticated_snapshot"] == "FAILED"
    assert result["reason"] == "SNAPSHOT_SCHEMA_OR_REDACTION_INVALID"
    assert "SECRET" not in json.dumps(result)


def test_false_model_residency_evidence_is_rejected():
    bad = _snapshot()
    bad["providers"][0]["resident_model_identity_verified"] = True
    assert gate.check("http://127.0.0.1:8088", token=TOKEN, opener=FakeOpener(snapshot=bad))[
        "authenticated_snapshot"
    ] == "FAILED"


def test_auth_denial_never_falls_back_to_anonymous():
    transport = FakeOpener(auth_status=403)
    report = gate.check("http://127.0.0.1:8088", token=TOKEN, opener=transport)
    assert report["reason"] == "AUTHENTICATED_SNAPSHOT_NOT_AVAILABLE"
    assert len(transport.calls) == 5


def test_redirect_handler_is_explicitly_disabled():
    assert gate._DenyRedirect().redirect_request(None, None, 301, None, {}, "http://elsewhere") is None
