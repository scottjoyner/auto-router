"""Strictly offline tests for redacted live Router/AssistX client preflight."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from scripts import inspect_live_router_client_preflight as m

IMAGE = "sha256:" + "a" * 64
SECRET = "SYNTHETIC_PRIVATE_SENTINEL_DO_NOT_LOG"


def record(name: str) -> dict:
    env = [f"{m.CREDENTIAL}={SECRET}"]
    if name == m.ROUTER:
        env += ["AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED=true"]
    else:
        env += ["AUTO_ROUTER_BASE_URL=http://host.docker.internal:8088"]
    return {
        "Name": "/" + name,
        "Image": IMAGE,
        "Config": {"Env": env},
        "State": {"Status": "running"},
        "HostConfig": {
            "ExtraHosts": ["host.docker.internal:host-gateway"],
            "PortBindings": {"8088/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8088"}]},
        },
    }


def baseline():
    return {name: record(name) for name in (m.ROUTER, *m.CLIENTS)}


def test_valid_credential_and_host_target_readback_remains_hold():
    result = m.evaluate(baseline(), IMAGE)
    assert result["verdict"] == "CLIENT_CONFIG_PARITY_OBSERVED_HOLD"
    assert result["reasons"] == []
    assert len(result["clients"]) == 3
    assert all(x["admin_token_matches_router"] for x in result["clients"])
    assert all(x["configured_router_target_matches_expected"] for x in result["clients"])
    assert result["production_cutover_allowed"] is False
    assert result["rollback_verified"] is False
    assert result["real_inference_client_compatibility_verified"] is False
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("change,code", [
    (("assistx-api", m.CREDENTIAL, "different-synthetic"), "CLIENT_ADMIN_TOKEN_MISMATCH"),
    (("assistx-worker", "AUTO_ROUTER_BASE_URL", "https://host.docker.internal:8088"),
     "CLIENT_ROUTER_TARGET_DRIFT"),
    (("assistx-hermes-adapter", "AUTO_ROUTER_BASE_URL", "http://evil.test:8088"),
     "CLIENT_ROUTER_TARGET_DRIFT"),
    (("assistx-api", "AUTO_ROUTER_BASE_URL", "http://user:pass@host.docker.internal:8088"),
     "CLIENT_ROUTER_TARGET_DRIFT"),
    (("assistx-api", "AUTO_ROUTER_BASE_URL", "http://host.docker.internal:8088/admin"),
     "CLIENT_ROUTER_TARGET_DRIFT"),
    ((m.ROUTER, "AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "false"), "EXECUTOR_AUTH_NOT_REQUIRED"),
])
def test_env_drift_denied(change, code):
    name, key, value = change
    d = baseline()
    d[name]["Config"]["Env"] = [
        (key + "=" + value) if x.startswith(key + "=") else x
        for x in d[name]["Config"]["Env"]
    ]
    result = m.evaluate(d, IMAGE)
    assert result["verdict"].endswith("_HOLD")
    assert any(reason.startswith(code) for reason in result["reasons"])
    assert result["production_cutover_allowed"] is False
    assert SECRET not in json.dumps(result)


def test_alias_removed_denied():
    d = baseline()
    d["assistx-api"]["HostConfig"]["ExtraHosts"] = []
    result = m.evaluate(d, IMAGE)
    assert "CLIENT_HOST_GATEWAY_ALIAS_MISSING:assistx-api" in result["reasons"]


def test_image_or_port_drift_denied():
    d = baseline()
    d[m.ROUTER]["Image"] = "sha256:" + "b" * 64
    d[m.ROUTER]["HostConfig"]["PortBindings"] = {}
    result = m.evaluate(d, IMAGE)
    assert "ROUTER_IMAGE_DRIFT" in result["reasons"]
    assert "ROUTER_PORT_CONTRACT_DRIFT" in result["reasons"]


def test_stopped_client_denied():
    d = baseline()
    d["assistx-worker"]["State"]["Status"] = "exited"
    result = m.evaluate(d, IMAGE)
    assert "CLIENT_NOT_RUNNING:assistx-worker" in result["reasons"]


def test_duplicate_admin_env_denied():
    d = baseline()
    d["assistx-api"]["Config"]["Env"].append(f"{m.CREDENTIAL}={SECRET}")
    with pytest.raises(m.PreflightDenied, match="duplicate"):
        m.evaluate(d, IMAGE)


def test_invalid_expected_image_denied():
    with pytest.raises(m.PreflightDenied, match="pinned"):
        m.evaluate(baseline(), "latest")


@pytest.mark.parametrize("name", ["other-container", "assistx-api;echo secret", ""])
def test_arbitrary_container_inspection_denied_without_subprocess(monkeypatch, name):
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k:
                        (_ for _ in ()).throw(AssertionError("must not run")))
    with pytest.raises(m.PreflightDenied, match="unapproved"):
        m.read_docker_inspect(name)


def test_docker_read_only_exact_argv_and_no_secret_output(monkeypatch):
    calls = []
    def fake(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps([record(argv[2])]))
    monkeypatch.setattr(m.subprocess, "run", fake)
    for name in (m.ROUTER, *m.CLIENTS):
        assert m.read_docker_inspect(name)["Name"] == "/" + name
    assert all(a == ["docker", "inspect", a[2]] for a, _ in calls)
    assert all(k["capture_output"] and k["timeout"] == 8 for _, k in calls)


def test_wrong_docker_identity_denied():
    d = record("assistx-api")
    d["Name"] = "/something-else"
    def fake(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps([d]))
    old = m.subprocess.run
    try:
        m.subprocess.run = fake
        with pytest.raises(m.PreflightDenied, match="wrong Docker container"):
            m.read_docker_inspect("assistx-api")
    finally:
        m.subprocess.run = old


def test_docker_failure_never_logs_stderr_secret(monkeypatch):
    def fake(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr=SECRET)
    monkeypatch.setattr(m.subprocess, "run", fake)
    with pytest.raises(m.PreflightDenied) as exc:
        m.read_docker_inspect(m.ROUTER)
    assert SECRET not in str(exc.value)


def test_main_json_safe_and_holds_even_with_full_parity(monkeypatch, capsys):
    monkeypatch.setattr(m, "read_docker_inspect", lambda name: record(name))
    monkeypatch.setattr(sys, "argv", ["preflight", "--expected-running-image", IMAGE])
    assert m.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == "CLIENT_CONFIG_PARITY_OBSERVED_HOLD"
    assert not report["production_cutover_allowed"]
    assert SECRET not in json.dumps(report)


def test_main_fail_closed_on_docker_error(monkeypatch, capsys):
    def deny(name):
        raise m.PreflightDenied(SECRET)
    monkeypatch.setattr(m, "read_docker_inspect", deny)
    monkeypatch.setattr(sys, "argv", ["preflight", "--expected-running-image", IMAGE])
    assert m.main() == 2
    payload = capsys.readouterr().out
    assert SECRET not in payload
    assert "UNAVAILABLE_HOLD" in payload
