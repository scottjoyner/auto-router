"""Offline unit tests for the read-only ingress acceptance tool."""
from __future__ import annotations

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

SRC = Path(__file__).resolve().parents[1] / "tools" / "router_admin_ingress_audit.py"
SPEC = importlib.util.spec_from_file_location("router_admin_ingress_audit", SRC)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


@pytest.mark.parametrize("raw", [
    "http://user:pass@localhost:8088", "http://localhost:8088/?token=x",
    "http://localhost:8088/admin/providers", "http://localhost:8088/#x",
    "file:///etc/passwd", "https://localhost:not-a-port", "http://localhost:8088 bad",
])
def test_rejects_credentialed_or_ambiguous_origin(raw):
    with pytest.raises(ValueError):
        mod.normalize_target(raw)


def test_accepts_ipv4_and_ipv6_origins():
    assert mod.normalize_target("http://127.0.0.1:8088/") == "http://127.0.0.1:8088"
    assert mod.normalize_target("http://[::1]:8088") == "http://[::1]:8088"


def test_fail_closed_when_live_legacy_get_is_anonymous_200():
    def statuses(origin, path, timeout):
        return (200, None) if path == "/admin/context" else (401 if path != "/health" else 200, None)
    with patch.object(mod, "probe_status", side_effect=statuses):
        result = mod.audit(["http://127.0.0.1:8088"])
    assert result["probe_result"] == "FAIL"
    assert result["release_decision"] == "HOLD"
    assert result["origins"][0]["probes"][2]["status"] == 200


def test_401_and_403_pass_but_release_stays_hold():
    def statuses(origin, path, timeout):
        return (200 if path == "/health" else 403, None)
    with patch.object(mod, "probe_status", side_effect=statuses):
        result = mod.audit(["http://127.0.0.1:8088", "http://[::1]:8088"])
    assert result["probe_result"] == "PASS"
    assert result["release_decision"] == "HOLD"
    assert len(result["origins"]) == 2


def test_redirect_and_unreachable_are_never_success():
    with patch.object(mod, "probe_status", return_value=(302, None)):
        assert mod.audit(["http://127.0.0.1:8088"])["probe_result"] == "FAIL"
    with patch.object(mod, "probe_status", return_value=(None, "URLError")):
        assert mod.audit(["http://127.0.0.1:8088"])["probe_result"] == "FAIL"


def test_main_prints_no_secret_and_never_approves_release():
    def statuses(origin, path, timeout):
        return (200 if path == "/health" else 401, None)
    output = io.StringIO()
    with patch.object(mod, "probe_status", side_effect=statuses), redirect_stdout(output):
        rc = mod.main(["--target", "http://127.0.0.1:8088"])
    data = json.loads(output.getvalue())
    assert rc == 0
    assert data["credentials_sent"] is False
    assert data["release_decision"] == "HOLD"
    assert all(set(x) == {"path", "status", "result", "error_type"} for x in data["origins"][0]["probes"])


def test_only_expected_safe_get_paths_and_fail_closed_image_not_implied():
    assert mod.ADMIN_GETS == ("/admin/providers", "/admin/context", "/admin/agent-jobs")
    assert "no Docker peer/WAN/IPv6" in mod.audit([])["limitations"]


def test_extended_admin_inventory_has_15_unique_mounted_static_get_paths():
    paths = mod.EXTENDED_ADMIN_GETS
    assert len(paths) == 15
    assert len(paths) == len(set(paths))
    assert paths[:len(mod.ADMIN_GETS)] == mod.ADMIN_GETS
    assert all(path.startswith("/admin/") and "{" not in path
               and "?" not in path for path in paths)
    assert "/admin/quota" in paths
    assert "/admin/runtime-projection" in paths


def test_extended_gate_fails_on_other_legacy_admin_get():
    def statuses(origin, path, timeout):
        if path == "/health":
            return 200, None
        if path == "/admin/quota":
            return 200, None
        return 401, None
    with patch.object(mod, "probe_status", side_effect=statuses):
        result = mod.audit(["http://127.0.0.1:8088"],
                           paths=mod.EXTENDED_ADMIN_GETS)
    assert result["probe_result"] == "FAIL"
    assert result["release_decision"] == "HOLD"
    assert result["admin_paths_checked"] == list(mod.EXTENDED_ADMIN_GETS)


def test_extended_cli_checks_every_admin_path_with_no_credentials():
    called = []
    def statuses(origin, path, timeout):
        called.append(path)
        return (200 if path == "/health" else 403), None
    output = io.StringIO()
    with patch.object(mod, "probe_status", side_effect=statuses), redirect_stdout(output):
        rc = mod.main(["--target", "http://127.0.0.1:8088", "--extended"])
    result = json.loads(output.getvalue())
    assert rc == 0
    assert len(called) == 16
    assert result["credentials_sent"] is False
    assert result["release_decision"] == "HOLD"
    assert result["probe_result"] == "PASS"


def test_admin_method_contract_includes_exact_mounted_paths():
    methods = mod.EXPECTED_ADMIN_METHODS
    assert len(methods) == 19
    assert {path for method, path in methods if method == "GET"} == set(mod.EXTENDED_ADMIN_GETS)
    assert {path for method, path in methods if method == "POST"} == {
        "/admin/load", "/admin/circuits/reset",
        "/admin/circuits/reset/guard-probe", "/admin/runtime-projection",
    }
    assert all(method in {"GET", "POST"} and path.startswith("/admin/")
               for method, path in methods)
