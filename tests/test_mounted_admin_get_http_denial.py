"""Actual mounted FastAPI /admin GET denial contract, without app lifespan.

The isolated subprocess uses synthetic authentication and offline in-memory
configuration. No live providers, Docker, SSH, Redis, SQL, or graph connected.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_every_mounted_admin_method_denies_anonymous_and_wrong_token():
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update({
        "AUTO_ROUTER_STRICT_OFFLINE": "true",
        "AUTO_ROUTER_DATABASE_URL": "sqlite:///:memory:",
        "AUTO_ROUTER_DATABASE_PLACEMENT_REQUIRED": "false",
        "AUTO_ROUTER_DATABASE_PLACEMENT_TEST_CONTEXT": "true",
        "PYTHONPATH": str(root / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    # Only checked-in constant Python runs; no network, real runtime startup,
    # credential reads, live endpoint calls or lifespan worker scheduling.
    script = r"""
from types import SimpleNamespace
import re
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute, iter_route_contexts
import auto_router.security as security
from auto_router.secure_live import app, assert_mounted_admin_guards

assert_mounted_admin_guards(app)
security.get_settings = lambda: SimpleNamespace(
    admin_token="synthetic-independent-operator-credential"
)
routes = set()
for context in iter_route_contexts(app.routes):
    leaf = context.route
    path = context.path or ""
    if isinstance(leaf, APIRoute) and path.startswith("/admin/"):
        for method in leaf.methods.intersection({"GET", "POST", "PUT", "PATCH", "DELETE"}):
            # This is synthetic only; never use a real object identifier.
            routes.add((method, re.sub(r"\\{[^{}]+\\}", "guard-probe", path)))
assert 19 <= len(routes) <= 64, sorted(routes)
must_exist = {
    ("GET", "/admin/providers"),
    ("GET", "/admin/context"),
    ("GET", "/admin/agent-jobs"),
    ("POST", "/admin/load"),
    ("POST", "/admin/circuits/reset"),
    ("POST", "/admin/circuits/reset/guard-probe"),
    ("POST", "/admin/runtime-projection"),
}
assert must_exist <= routes, sorted(must_exist - routes)

client = TestClient(app, raise_server_exceptions=False)
for method, path in sorted(routes):
    for headers in ({}, {"X-Admin-Token": "synthetic-wrong-token"}):
        result = client.request(method, path, headers=headers, follow_redirects=False)
        assert result.status_code in (401, 403), (method, path, result.status_code)
print("MOUNTED_ADMIN_ALL_METHOD_NEGATIVE_PASS", len(routes), len(routes) * 2)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=root, env=env, capture_output=True, text=True, timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:] + result.stdout[-1000:]
    assert "MOUNTED_ADMIN_ALL_METHOD_NEGATIVE_PASS" in result.stdout
