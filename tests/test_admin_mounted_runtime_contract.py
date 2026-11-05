"""Separate-process synthetic gate for both supported Auto-Router app entrypoints."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_mounted_admin_gate.py"


@pytest.mark.parametrize(
    ("entrypoint", "expected_count"),
    [("main", 13), ("main_live", 19), ("secure_live", 19)],
)
def test_all_mounted_admin_routes_require_auth_before_execution(entrypoint,expected_count):
    env = os.environ.copy()
    env["AUTO_ROUTER_STRICT_OFFLINE"] = "true"
    # Production secure_live installs the idempotency ledger on import.
    # Only use an in-memory SQLite fixture, never the container's /data
    # mount or this host's production database.
    env["AUTO_ROUTER_DATABASE_URL"] = "sqlite:///:memory:"
    env["AUTO_ROUTER_DATABASE_PLACEMENT_REQUIRED"] = "false"
    env["AUTO_ROUTER_DATABASE_PLACEMENT_TEST_CONTEXT"] = "true"
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--entrypoint", entrypoint],
        capture_output=True,
        text=True,
        env=env,
        timeout=18,
        check=False,
    )
    assert result.returncode == 0, (
        f"isolated {entrypoint} guard failed: "
        f"{result.stderr[-1200:]} / {result.stdout[-1000:]}"
    )
    # Logging may appear on stdout. The last line is only the scripted JSON.
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["mounted_routes"] == expected_count
    assert payload["checked_method_paths"] == expected_count
    assert payload["no_lifespan"] is True
    assert payload["authenticated_mutations"] is False
    assert payload["production_status"] == "NO_GO"
    assert all(row["status"] == 401 for row in payload["denied"])
    assert {"GET","POST"} <= {row["method"] for row in payload["denied"]}
    assert any(row["path"] == "/admin/providers" for row in payload["denied"])
    assert any(row["path"] == "/admin/circuits/reset" and row["method"] == "POST"
               for row in payload["denied"])


def test_our_no_lifespan_checker_rejects_unprotected_synthetic_admin_route():
    # In-process fake app with a deliberately unauthenticated route.
    from fastapi import FastAPI

    from scripts.audit_mounted_admin_gate import audit

    app=FastAPI()

    @app.post("/admin/mock-unprotected")
    async def mock_unprotected():
        raise RuntimeError("must never be executed by a guard audit")

    with pytest.raises(AssertionError,match="missing compiled admin guard"):
        audit(app)


def test_production_entrypoint_blocks_new_unguarded_or_raw_admin_routes():
    """Synthetic startup-contract test: no lifespan, no network, no credentials."""
    env = os.environ.copy()
    env["AUTO_ROUTER_STRICT_OFFLINE"] = "true"
    env["AUTO_ROUTER_DATABASE_URL"] = "sqlite:///:memory:"
    env["AUTO_ROUTER_DATABASE_PLACEMENT_REQUIRED"] = "false"
    env["AUTO_ROUTER_DATABASE_PLACEMENT_TEST_CONTEXT"] = "true"
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    code = """
from fastapi import Depends, FastAPI
from starlette.responses import PlainTextResponse
from starlette.routing import Route, Mount
from auto_router.secure_live import app, assert_mounted_admin_guards
from auto_router.security import require_admin

assert_mounted_admin_guards(app)
new = FastAPI()
@new.get('/admin/synthetic-safe', dependencies=[Depends(require_admin)])
async def safe():
    return {}
assert_mounted_admin_guards(new)

@new.get('/admin/synthetic-unguarded')
async def unsafe():
    raise AssertionError('must never reach handler')
try:
    assert_mounted_admin_guards(new)
except RuntimeError as exc:
    assert 'lacks compiled admin authentication' in str(exc)
else:
    raise AssertionError('unprotected route accepted')

raw = FastAPI()
raw.router.routes.append(Route('/admin/raw', lambda request: PlainTextResponse('unsafe')))
try:
    assert_mounted_admin_guards(raw)
except RuntimeError as exc:
    assert 'unsupported mounted admin route type' in str(exc)
else:
    raise AssertionError('raw Starlette admin route accepted')

mounted = FastAPI()
mounted.mount('/admin/private', FastAPI())
try:
    assert_mounted_admin_guards(mounted)
except RuntimeError as exc:
    assert 'unsupported mounted admin route type' in str(exc)
else:
    raise AssertionError('mounted nested admin app accepted')

# Shadow routes whose *declared* path does not start with /admin still
# capture privileged requests before they reach a protected endpoint.
shadow = FastAPI()
shadow.router.routes.insert(
    0, Route('/{remainder:path}', lambda request: PlainTextResponse('shadow'))
)
@shadow.get('/admin/only-safe', dependencies=[Depends(require_admin)])
async def protected_after_shadow():
    return {}
try:
    assert_mounted_admin_guards(shadow)
except RuntimeError as exc:
    assert 'unsupported mounted admin route type' in str(exc)
else:
    raise AssertionError('catch-all route shadowing admin was accepted')

rootmount = FastAPI()
rootmount.router.routes.insert(0, Mount('/', app=FastAPI()))
@rootmount.get('/admin/only-safe', dependencies=[Depends(require_admin)])
async def protected_after_mount():
    return {}
try:
    assert_mounted_admin_guards(rootmount)
except RuntimeError as exc:
    assert 'unsupported mounted admin route type' in str(exc)
else:
    raise AssertionError('root mount shadowing admin was accepted')

# A similarly broad FastAPI route with the actual compiled guard is allowed.
# The test is about authorization, not requiring a particular route layout.
guarded_catchall = FastAPI()
@guarded_catchall.get('/{remainder:path}', dependencies=[Depends(require_admin)])
async def guarded():
    return {}
@guarded_catchall.get('/admin/known', dependencies=[Depends(require_admin)])
async def guarded_known():
    return {}
assert_mounted_admin_guards(guarded_catchall)

empty = FastAPI()
try:
    assert_mounted_admin_guards(empty)
except RuntimeError as exc:
    assert 'admin surface missing' in str(exc)
else:
    raise AssertionError('missing admin route accepted')
print('admin-route-import-fence-pass')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True, text=True, env=env, timeout=18, check=False,
    )
    assert result.returncode == 0, (
        f"synthetic production route fence failed: {result.stderr[-1300:]}"
    )
    assert "admin-route-import-fence-pass" in result.stdout
