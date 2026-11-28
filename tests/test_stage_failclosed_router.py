"""Synthetic safety contract for the isolated, disposable router stage runner."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

STAGE = Path(__file__).resolve().parents[1] / "scripts" / "stage_failclosed_router.py"
spec = importlib.util.spec_from_file_location("stage_failclosed_router_test", STAGE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def isolated_inspect(*, network="none", readonly=True, mounts=None, ports=None):
    return {
        "State": {"Status": "running"},
        "HostConfig": {"NetworkMode": network, "ReadonlyRootfs": readonly, "PortBindings": ports},
        "Config": {"ExposedPorts": {"8088/tcp": {}}},
        "Mounts": mounts if mounts is not None else [{"Destination": "/candidate", "RW": False}],
    }


def test_deployment_command_never_connects_to_fleet(monkeypatch):
    commands = []

    def fake_docker(*args, **kwargs):
        commands.append(args)
        if args[:2] == ("image", "inspect"):
            return "sha256:" + "a" * 64
        return "synthetic"

    def nonexistent():
        raise subprocess.CalledProcessError(1, ["docker", "inspect"])

    monkeypatch.setattr(module, "docker", fake_docker)
    monkeypatch.setattr(module, "get_inspect", nonexistent)
    module.deploy()
    run = next(c for c in commands if c[0] == "run")
    assert "--network" in run and run[run.index("--network") + 1] == "none"
    assert "--read-only" in run
    assert "--cap-drop" in run and run[run.index("--cap-drop") + 1] == "ALL"
    assert "--user" in run and run[run.index("--user") + 1] == "65534:65534"
    assert "--security-opt" in run
    assert "--publish" not in run and "-p" not in run and "--network-alias" not in run
    assert "--mount" in run and "readonly" in run[run.index("--mount") + 1]
    assert run[-8] == "sha256:" + "a" * 64
    assert module.ENV["AUTO_ROUTER_AUTOLOAD_ENABLED"] == "false"
    assert module.ENV["AUTO_ROUTER_PLACEMENT_UNLOAD"] == "false"
    assert module.ENV["AUTO_ROUTER_FLEET_DISPATCHER_ENABLED"] == "false"
    assert module.ENV["AUTO_ROUTER_RUNTIME_PROJECTION_URL"] == ""


@pytest.mark.parametrize(
    "override",
    [
        {"network": "bridge"},
        {"readonly": False},
        {"ports": {"8088/tcp": [{}]}},
        {"mounts": [{"Destination": "/candidate", "RW": True}]},
        {"mounts": [{"Destination": "/production", "RW": False}]},
    ],
)
def test_verification_refuses_unsealed_container(monkeypatch, override):
    state = isolated_inspect(**override)
    monkeypatch.setattr(module, "get_inspect", lambda: state)
    monkeypatch.setattr(
        module,
        "docker",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("never exec an unsealed candidate")),
    )
    with pytest.raises(RuntimeError):
        module.verify()


def test_destroy_refuses_nonstaging_network(monkeypatch):
    monkeypatch.setattr(module, "get_inspect", lambda: isolated_inspect(network="bridge"))
    monkeypatch.setattr(
        module,
        "docker",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("must not remove a nonsealed container")
        ),
    )
    with pytest.raises(RuntimeError):
        module.destroy()


def test_admin_basic_identity_and_constant_time_token_gate(monkeypatch):
    from types import SimpleNamespace

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from auto_router import security

    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(admin_token="synthetic-test-admin-secret"),
    )
    app = FastAPI()

    @app.get("/admin/probe")
    async def probe(_: None = Depends(security.require_admin)):
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/admin/probe").status_code == 401
    assert (
        client.get("/admin/probe", auth=("other", "synthetic-test-admin-secret")).status_code == 401
    )
    assert (
        client.get("/admin/probe", auth=("admin", "synthetic-test-admin-secret")).status_code == 200
    )
    assert (
        client.get(
            "/admin/probe", headers={"X-Admin-Token": "synthetic-test-admin-secret"}
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/admin/probe", headers={"Authorization": "Bearer synthetic-test-admin-secret"}
        ).status_code
        == 200
    )
    assert client.get("/admin/probe", headers={"Authorization": "Bearer wrong"}).status_code == 401
    client.close()


def test_staging_refuses_checkout_with_dotenv_credentials(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SENTINEL_DO_NOT_MOUNT=1\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/providers.reconciliation.yaml").write_text("{}\n")
    monkeypatch.setattr(module, "ROOT", tmp_path)

    def fake_inspect():
        raise subprocess.CalledProcessError(1, ["docker", "inspect"])

    monkeypatch.setattr(module, "get_inspect", fake_inspect)
    monkeypatch.setattr(
        module,
        "docker",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("must reject secret before docker image or container operations")
        ),
    )
    with pytest.raises(RuntimeError, match="credential .env"):
        module.deploy()
