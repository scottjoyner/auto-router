#!/usr/bin/env python3
"""Deploy/verify/remove a sealed, networkless Auto-Router safety canary.

Never connects to AssistX, Redis, Tailscale, Neo4j or inference endpoints.
The tested checkout is bound read-only; writable state is tmpfs and discarded.
This is a STAGING probe, not a production promotion or a failover controller.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
from typing import Any

NAME = "router-pr71-stage-20261017"
ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGE = "git-router:latest"
ENV = {
    "PYTHONPATH": "/candidate/src",
    "AUTO_ROUTER_STRICT_OFFLINE": "true",
    "AUTO_ROUTER_PROVIDER_CONFIG": "/candidate/config/providers.reconciliation.yaml",
    "AUTO_ROUTER_POLICY_CONFIG": "/candidate/config/policies.yaml",
    "AUTO_ROUTER_AGENT_CONFIG": "/candidate/config/agent_workers.yaml",
    "AUTO_ROUTER_CONTEXT_CONFIG": "/candidate/config/context.example.yaml",
    "AUTO_ROUTER_DATABASE_URL": "sqlite:////data/staging.sqlite3",
    "AUTO_ROUTER_DATABASE_PLACEMENT_REQUIRED": "true",
    "AUTO_ROUTER_MEMORY_ENABLED": "false",
    "AUTO_ROUTER_AUTOLOAD_ENABLED": "false",
    "AUTO_ROUTER_PLACEMENT_UNLOAD": "false",
    "AUTO_ROUTER_FLEET_DISPATCHER_ENABLED": "false",
    "AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED": "true",
    "AUTO_ROUTER_ASSISTX_EVENT_SINK_URL": "",
    "AUTO_ROUTER_ASSISTX_TASKS_URL": "",
    "AUTO_ROUTER_RUNTIME_PROJECTION_URL": "",
    "AUTO_ROUTER_REDIS_URL": "redis://127.0.0.1:6399/0",
    "AUTO_ROUTER_INTERNAL_SERVICE_TOKEN": "disposable-staging-only-not-a-prod-credential",
    "AUTO_ROUTER_ADMIN_TOKEN": "",
    "AUTO_ROUTER_ROUTE_DECISION_TOKEN": "",
}


def docker(*args: str, input: str | None = None) -> str:
    result = subprocess.run(
        ["docker", *args],
        input=input,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_inspect() -> dict[str, Any]:
    return json.loads(docker("inspect", NAME))[0]


def deploy() -> None:
    try:
        get_inspect()
    except subprocess.CalledProcessError:
        pass
    else:
        raise RuntimeError(f"{NAME} already exists; refusing to replace any container")
    if (ROOT / ".env").exists():
        raise RuntimeError("refusing to mount a checkout containing a credential .env file")
    if not (ROOT / "config/providers.reconciliation.yaml").is_file():
        raise RuntimeError("missing checkout-specific offline provider fixture")
    image_id = docker("image", "inspect", IMAGE, "--format", "{{.Id}}")
    if not image_id.startswith("sha256:"):
        raise RuntimeError("refusing an unpinned image identity")
    cmd = [
        "run",
        "-d",
        "--name",
        NAME,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65534:65534",
        "--pids-limit",
        "80",
        "--memory",
        "600m",
        "--cpus",
        "1",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=64m",
        "--tmpfs",
        "/data:rw,nosuid,nodev,size=64m",
        "--mount",
        f"type=bind,source={ROOT},target=/candidate,readonly",
        "--workdir",
        "/candidate",
    ]
    for key, val in sorted(ENV.items()):
        cmd.extend(("--env", f"{key}={val}"))
    cmd.extend(
        (
            "--entrypoint",
            "python",
            image_id,
            "-m",
            "uvicorn",
            "auto_router.secure_live:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8088",
        )
    )
    print(json.dumps({"stage": NAME, "image_id": image_id, "network": "none"}))
    print(docker(*cmd))


def verify() -> None:
    state = get_inspect()
    host = state["HostConfig"]
    if state["State"]["Status"] != "running":
        raise RuntimeError("staging canary is not running")
    if host["NetworkMode"] != "none" or not host["ReadonlyRootfs"]:
        raise RuntimeError("staging isolation violation: network/root filesystem")
    if host.get("PortBindings") or state["Config"].get("ExposedPorts"):
        # Docker image EXPOSE alone is inert, but publishing must be impossible.
        if host.get("PortBindings"):
            raise RuntimeError("staging is publishing ports")
    mounts = state["Mounts"]
    if len(mounts) != 1 or mounts[0]["Destination"] != "/candidate" or mounts[0]["RW"]:
        raise RuntimeError("unexpected host filesystem mount in staging")
    checks = """
import urllib.error,urllib.request
for method,path,expected in [
    ("GET","/health",200),("GET","/admin/providers",401),
    ("GET","/admin/context/graph",401),("GET","/admin/usage",401),
    ("POST","/admin/load",401),("POST","/admin/circuits/reset",401),
    ("POST","/api/routes/request",401)]:
    try:
        payload=b'{"correlation_id":"staging-safety"}' if path=="/api/routes/request" else None
        request=urllib.request.Request(
            "http://127.0.0.1:8088"+path,method=method,data=payload,
            headers={"Content-Type":"application/json"} if payload else {})
        response=urllib.request.urlopen(request,timeout=2)
        actual=response.status
    except urllib.error.HTTPError as exc:
        actual=exc.code
    assert actual==expected,(method,path,actual,expected)
    print(method,path,actual)
print("SEALED_STAGING_PASS")
"""
    for attempt in range(10):
        try:
            output = docker("exec", "-i", NAME, "python", "-c", checks)
            print(output)
            return
        except subprocess.CalledProcessError:
            if attempt == 9:
                raise
            time.sleep(1)


def destroy() -> None:
    state = get_inspect()
    if state["HostConfig"]["NetworkMode"] != "none":
        raise RuntimeError("refusing to remove a container outside sealed staging network")
    print(docker("rm", "-f", NAME))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["deploy", "verify", "destroy"])
    args = parser.parse_args()
    {"deploy": deploy, "verify": verify, "destroy": destroy}[args.action]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
