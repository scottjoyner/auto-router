"""Verify the complete mounted admin HTTP method matrix in isolated child Python.

The pytest module itself does not import the Router. A temporary, strictly
synthetic child process owns database state, settings and app imports.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SYNTHETIC_TOKEN = "synthetic-matrix-test-only"


def _child_check() -> int:
    # Only called from a freshly configured child with synthetic credentials.
    from fastapi.testclient import TestClient

    from auto_router.secure_live import app

    methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    routes = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if isinstance(path, str) and (
            path == "/admin" or path.startswith("/admin/")
        ):
            for method in sorted(methods.intersection(
                getattr(route, "methods", None) or (),
            )):
                routes.append((
                    method,
                    path,
                    re.sub(r"\{[^{}]+\}", "synthetic-id", path),
                ))

    assert len(routes) >= 19, "registered admin surface unexpectedly shrank"
    paths = {route for _, route, _ in routes}
    assert {
        "/admin/providers",
        "/admin/context",
        "/admin/agent-jobs",
        "/admin/load",
    }.issubset(paths), "critical admin routes missing"

    with TestClient(app, raise_server_exceptions=False) as client:
        denied = [
            client.request(method, target).status_code
            for method, _, target in routes
        ]
        correct = client.get(
            "/admin/providers",
            headers={"X-Admin-Token": SYNTHETIC_TOKEN},
        )
        wrong = client.get(
            "/admin/providers",
            headers={"X-Admin-Token": "wrong-synthetic"},
        )
        duplicate = client.get("/admin/providers", headers=[
            ("X-Admin-Token", SYNTHETIC_TOKEN),
            ("X-Admin-Token", SYNTHETIC_TOKEN),
        ])

    assert all(status == 401 for status in denied), "anonymous admin route admitted"
    assert correct.status_code == 200, "legitimate synthetic operator rejected"
    assert wrong.status_code == 401, "wrong synthetic credential admitted"
    assert duplicate.status_code == 401, "ambiguous headers accepted"
    assert correct.headers.get("content-type", "").startswith("application/json")
    assert SYNTHETIC_TOKEN not in correct.text, "admin credential leaked in response"
    print("MATRIX_RESULT " + json.dumps({
        "tested": len(routes),
        "unauthenticated_401": len(denied),
        "valid_operator": correct.status_code,
        "invalid_operator": wrong.status_code,
        "duplicate_header": duplicate.status_code,
        "token_reflected": False,
    }, sort_keys=True))
    return 0


def test_full_admin_matrix_offline_child():
    with tempfile.TemporaryDirectory(prefix="router-admin-matrix-") as tmp:
        path = Path(tmp)
        # Explicit allowlist, never forward Router or provider credential env.
        env = {
            key: val for key, val in os.environ.items()
            if key in {
                "PATH", "PYTHONPATH", "VIRTUAL_ENV", "HOME",
                "LANG", "LC_ALL", "TMPDIR",
            }
        }
        env.update({
            "AUTO_ROUTER_ADMIN_TOKEN": SYNTHETIC_TOKEN,
            "AUTO_ROUTER_DATABASE_URL": "sqlite:///" + str(path / "ledger.sqlite3"),
            "AUTO_ROUTER_DATABASE_PERSISTENT_ROOT": str(path),
            "AUTO_ROUTER_STRICT_OFFLINE": "true",
            "AUTO_ROUTER_FLEET_DISPATCHER_ENABLED": "false",
        })
        run = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--matrix-child"],
            env=env,
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
            timeout=35,
        )
        assert run.returncode == 0, (
            "synthetic admin matrix child failed: " + run.stderr[-800:]
        )
        lines = [
            line.removeprefix("MATRIX_RESULT ")
            for line in run.stdout.splitlines()
            if line.startswith("MATRIX_RESULT ")
        ]
        assert len(lines) == 1, "missing or duplicated test result"
        receipt = json.loads(lines[0])
        assert receipt["tested"] >= 19
        assert receipt["unauthenticated_401"] == receipt["tested"]
        assert receipt["valid_operator"] == 200
        assert receipt["invalid_operator"] == 401
        assert receipt["duplicate_header"] == 401
        assert receipt["token_reflected"] is False


if __name__ == "__main__":
    if sys.argv[1:] != ["--matrix-child"]:
        raise SystemExit("HOLD: run through pytest")
    raise SystemExit(_child_check())
