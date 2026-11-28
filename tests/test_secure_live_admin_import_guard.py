"""Synthetic startup regression for the real production ASGI entrypoint.

No lifespan is started, no external service is contacted, and no actual
operator credential or provider configuration is read. A deliberately
unguarded route must fail during the same guard used by secure_live import.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_secure_live_starts_only_with_compiled_mounted_admin_guards():
    # Run in an isolated subprocess: importing the production entrypoint
    # constructs router state; never reuse a running service's environment.
    env = {k: os.environ[k] for k in ("PATH", "HOME", "SYSTEMROOT", "WINDIR")
           if k in os.environ}
    env.update({
        "AUTO_ROUTER_STRICT_OFFLINE": "true",
        "AUTO_ROUTER_DATABASE_URL": "sqlite:///:memory:",
        "AUTO_ROUTER_DATABASE_PLACEMENT_REQUIRED": "false",
        "AUTO_ROUTER_DATABASE_PLACEMENT_TEST_CONTEXT": "true",
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    code = r"""
from fastapi import APIRouter, Depends, FastAPI
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from auto_router.security import require_admin
from auto_router.secure_live import app, assert_mounted_admin_guards

# Must not start production lifespan tasks or issue actual HTTP requests.
assert_mounted_admin_guards(app)

allowed = FastAPI()
@allowed.get('/admin/guarded', dependencies=[Depends(require_admin)])
async def guarded():
    return {'safe': True}
assert_mounted_admin_guards(allowed)

unprotected = FastAPI()
@unprotected.get('/admin/unguarded')
async def unguarded():
    raise AssertionError('unprotected handler must never execute')
try:
    assert_mounted_admin_guards(unprotected)
except RuntimeError as exc:
    assert 'lacks compiled admin authentication' in str(exc)
else:
    raise AssertionError('unguarded FastAPI route passed startup gate')

inherited = FastAPI()
r = APIRouter()
@r.get('/private')
async def included_unguarded():
    raise AssertionError('unguarded included router handler must never execute')
inherited.include_router(r, prefix='/admin')
try:
    assert_mounted_admin_guards(inherited)
except RuntimeError as exc:
    assert 'lacks compiled admin authentication' in str(exc)
else:
    raise AssertionError('unguarded included route passed startup gate')

raw = FastAPI()
raw.router.routes.append(Route('/admin/raw', lambda _: PlainTextResponse('unsafe')))
try:
    assert_mounted_admin_guards(raw)
except RuntimeError as exc:
    assert 'unsupported mounted admin route type' in str(exc)
else:
    raise AssertionError('raw Starlette admin route passed startup gate')

shadow = FastAPI()
shadow.router.routes.insert(
    0, Mount('/', app=FastAPI())
)
@shadow.get('/admin/guarded', dependencies=[Depends(require_admin)])
async def behind_shadow():
    return {}
try:
    assert_mounted_admin_guards(shadow)
except RuntimeError as exc:
    assert 'unsupported mounted admin route type' in str(exc)
else:
    raise AssertionError('raw root mount shadow passed startup gate')

retired = FastAPI()
@retired.post('/jobs/agent', dependencies=[Depends(require_admin)])
async def retired_route():
    raise AssertionError('retired agent job must never be invoked')
try:
    assert_mounted_admin_guards(retired)
except RuntimeError as exc:
    assert 'retired agent execution surface' in str(exc)
else:
    raise AssertionError('retired legacy agent claim was admitted')

print('MOUNTED_ROUTER_IMPORT_AUTH_GUARD_PASS')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=ROOT, env=env, capture_output=True, text=True,
        timeout=35, check=False,
    )
    assert result.returncode == 0, (
        "Synthetic secure_live entrypoint admission failed: "
        + result.stderr[-2200:] + result.stdout[-800:]
    )
    assert "MOUNTED_ROUTER_IMPORT_AUTH_GUARD_PASS" in result.stdout
