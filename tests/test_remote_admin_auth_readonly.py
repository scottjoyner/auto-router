"""Synthetic, no-network contracts for remote anonymous admin denial."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import pytest

SRC = Path(__file__).resolve().parents[1] / "scripts" / "verify_remote_admin_auth_readonly.py"
spec = importlib.util.spec_from_file_location("remote_admin_gate", SRC)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class FakeResponse:
    def __init__(self, code):
        self.status = code

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self, *_): raise AssertionError("response body MUST NOT be read")


class FakeOpen:
    def __init__(self, statuses):
        self.calls = []
        self.statuses = statuses

    def open(self, req, timeout):
        self.calls.append((req.full_url, req.get_method(), dict(req.headers), timeout))
        return FakeResponse(self.statuses[len(self.calls) - 1])


@pytest.mark.parametrize("good", ["100.101.102.103", "10.1.2.3", "172.19.3.9", "192.168.1.202"])
def test_explicit_private_ip_only(good):
    assert gate.validate_target(good) == good


@pytest.mark.parametrize("bad", [
    "localhost", "127.0.0.1", "::1", "8.8.8.8", "169.254.1.1",
    "100.63.4.1", "100.128.0.1", "0.0.0.0", "http://100.101.102.103",
    "10.0.0.1:8088", "1.1.1.1", "224.0.0.1",
])
def test_not_public_host_named_or_local(bad):
    with pytest.raises(ValueError):
        gate.validate_target(bad)


def test_six_anonymous_denials_are_required_and_no_bodies_read():
    fake = FakeOpen([401] * 6)
    result = gate.check("100.101.102.103", fake)
    assert result["decision"] == "PASS_REMOTE_401"
    assert result["credentials_sent"] is False
    assert result["response_bodies_read"] is False
    assert len(fake.calls) == 6
    assert all(method == "GET" and not headers and timeout == 4 for _, method, headers, timeout in fake.calls)
    assert all(url.startswith("http://100.101.102.103:8088/admin/") for url, *_ in fake.calls)


@pytest.mark.parametrize("route_index", range(6))
@pytest.mark.parametrize("unexpected_status", [200, 302, 403, 404, 503])
def test_any_remote_admin_route_with_non_401_holds(route_index, unexpected_status):
    codes = [401] * 6
    codes[route_index] = unexpected_status
    assert gate.check("100.101.102.103", FakeOpen(codes))["decision"] == "HOLD_REMOTE_ADMIN_AUTH"


def test_no_redirect_handler_forwards_to_new_origin():
    assert gate.NoRedirect().redirect_request(None, None, 302, None, {}, "http://other") is None
