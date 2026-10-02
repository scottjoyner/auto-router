"""Contract tests for the admin-auth surface and the manual-apply gate.

Two security fixes are pinned here:

* **X1** — every ``/admin/*``, ``/jobs/agent*``, ``/dashboard`` and ``/api/dashboard/*``
  route carried by ``main.py`` / ``ops_dashboard_routes.py`` now depends on
  ``require_admin``. Previously 16 of them were mounted with no gate at all, so a
  caller with no credentials could read the entire fleet topology and, via
  ``POST /admin/load``, load models onto nodes.
* **X2** — ``POST /admin/load?apply=true`` no longer bypasses the deployment's
  autoload posture. Realising a manual apply requires the separate
  ``AUTO_ROUTER_PLACEMENT_MANUAL_APPLY_ENABLED`` opt-in.

Both are checked against the *live* route table built from the production
entrypoint, so a future route added without the dependency is caught.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from auto_router.security import require_admin

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"

# Routes that are deliberately reachable without an admin token, and why.
UNGATED_ALLOWLIST: dict[str, str] = {
    "/health": "liveness probe",
    "/api/health": "liveness probe",
    "/metrics": "Prometheus scrape",
    # Inference paths are gated by ExecutorInferenceAuthMiddleware, not by a
    # route dependency -- a different mechanism, asserted separately below.
    "/v1/chat/completions": "executor_auth middleware",
    "/v1/completions": "executor_auth middleware",
    "/v1/embeddings": "executor_auth middleware",
    "/v1/responses": "executor_auth middleware",
    # AssistX-owned route intake, guarded by install_strict_assistx_route_guard.
    "/api/routes/request": "strict_assistx_routes lane guard",
}

_PROBE = """
import json, sys
import auto_router.secure_live as sl
from fastapi.routing import APIRoute

out = {}
for r in sl.app.router.routes:
    if isinstance(r, APIRoute):
        deps = {getattr(d.call, "__name__", str(d.call)) for d in r.dependant.dependencies}
        out.setdefault(r.path, []).append("require_admin" in deps)
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def production_route_gates(tmp_path_factory) -> dict[str, list[bool]]:
    """Build the real production app in a fresh interpreter.

    ``secure_live`` rebinds module globals at import time, so the route table has
    to be read in a subprocess to stay independent of test collection order.
    """

    import os

    env = dict(os.environ)
    env.update(
        AUTO_ROUTER_STRICT_OFFLINE="true",
        AUTO_ROUTER_PROVIDER_CONFIG=str(CONFIG_DIR / "providers.reconciliation.yaml"),
        AUTO_ROUTER_CONTEXT_CONFIG=str(CONFIG_DIR / "context.example.yaml"),
        AUTO_ROUTER_ADMIN_TOKEN="contract-test-token",
        AUTO_ROUTER_DATABASE_URL=f"sqlite:///{tmp_path_factory.mktemp('probe') / 'r.sqlite3'}",
    )
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    import json

    return json.loads(result.stdout.strip().splitlines()[-1])


def test_every_mounted_route_is_either_gated_or_allowlisted(
    production_route_gates: dict[str, list[bool]],
) -> None:
    ungated = {
        path for path, gated in production_route_gates.items() if not all(gated)
    }
    unexpected = ungated - set(UNGATED_ALLOWLIST)
    assert unexpected == set(), (
        f"routes mounted without require_admin and not on the allowlist: {sorted(unexpected)}"
    )


def test_allowlist_entries_actually_exist(production_route_gates: dict[str, list[bool]]) -> None:
    """A stale allowlist entry would hide a future rename."""

    missing = set(UNGATED_ALLOWLIST) - set(production_route_gates)
    assert missing == set(), f"allowlist names routes that no longer exist: {sorted(missing)}"


@pytest.mark.parametrize(
    "path",
    [
        "/admin/quota",
        "/admin/admission",
        "/admin/runtime-projection",
        "/admin/placement",
        "/admin/load",
        "/admin/context",
        "/admin/context/graph",
        "/admin/providers",
        "/admin/providers/health",
        "/admin/agent-workers",
        "/admin/agent-jobs",
        "/admin/usage",
        "/admin/circuits",
        "/admin/circuits/reset",
        "/admin/circuits/reset/{owner:path}",
        "/admin/memory",
        "/admin/ops/summary",
        "/admin/ops/preflight",
        "/dashboard",
        "/api/dashboard/summary",
        "/api/dashboard/ops-summary",
        "/v1/models",
        "/metrics/ops",
    ],
)
def test_fleet_disclosure_surfaces_require_admin(
    path: str,
    production_route_gates: dict[str, list[bool]],
) -> None:
    assert production_route_gates.get(path), f"{path} is not mounted at all"
    assert all(production_route_gates[path]), f"{path} is mounted without require_admin"


def test_health_and_metrics_stay_open(production_route_gates: dict[str, list[bool]]) -> None:
    for path in ("/health", "/api/health", "/metrics"):
        assert path in production_route_gates
        assert not any(production_route_gates[path]), f"{path} must stay unauthenticated"


def test_require_admin_fails_closed_when_no_token_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """security.py contract: no token configured means every request is rejected."""

    import asyncio

    from fastapi import HTTPException

    from auto_router.settings import Settings, get_settings

    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "")
    get_settings.cache_clear()
    try:
        settings = Settings()
        assert settings.admin_token == ""
        with pytest.raises(HTTPException) as caught:
            asyncio.run(
                require_admin(
                    request=None,
                    x_admin_token="anything",
                    bearer=None,
                    basic=None,
                )
            )
        assert caught.value.status_code == 401
        assert "not configured" in str(caught.value.detail)
    finally:
        get_settings.cache_clear()


def test_manual_apply_requires_the_separate_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """X2: the `apply` query parameter must not load models on its own authority."""

    import importlib

    monkeypatch.delenv("AUTO_ROUTER_PLACEMENT_MANUAL_APPLY_ENABLED", raising=False)
    module = importlib.reload(importlib.import_module("auto_router.model_placement"))
    assert module.MANUAL_APPLY_ENABLED is False

    monkeypatch.setenv("AUTO_ROUTER_PLACEMENT_MANUAL_APPLY_ENABLED", "true")
    module = importlib.reload(importlib.import_module("auto_router.model_placement"))
    assert module.MANUAL_APPLY_ENABLED is True

    # restore module state for other tests
    monkeypatch.delenv("AUTO_ROUTER_PLACEMENT_MANUAL_APPLY_ENABLED", raising=False)
    importlib.reload(importlib.import_module("auto_router.model_placement"))


def test_admin_load_refuses_apply_without_the_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: a request with ?apply=true must be refused by default."""

    import asyncio
    import importlib
    from types import SimpleNamespace

    import auto_router.model_placement as placement

    calls: list[bool] = []

    async def fake_reconcile(state, apply=None, swappable_only=False):
        calls.append(bool(apply))
        return {"applied": True}

    monkeypatch.setattr(placement, "MANUAL_APPLY_ENABLED", False)
    monkeypatch.setattr(placement, "AUTOLOAD_ENABLED", False)
    monkeypatch.setattr(placement, "compute_plan", lambda state: {"loads_needed": [], "unloads_needed": []})
    monkeypatch.setattr(placement, "reconcile_once", fake_reconcile)

    import auto_router.main as main_module

    admin_load = importlib.reload(main_module).admin_load
    state = SimpleNamespace()

    result = asyncio.run(admin_load(apply=True))

    assert calls == [], "reconcile_once must not run when the opt-in is absent"
    assert result["applied"] is False
    assert "AUTO_ROUTER_PLACEMENT_MANUAL_APPLY_ENABLED" in result["apply_refused"]


def test_admin_load_still_allows_dry_run_and_opted_in_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import importlib
    from types import SimpleNamespace

    import auto_router.model_placement as placement

    calls: list[bool | None] = []

    async def fake_reconcile(state, apply=None, swappable_only=False):
        calls.append(apply)
        return {"applied": bool(apply)}

    monkeypatch.setattr(placement, "MANUAL_APPLY_ENABLED", True)
    monkeypatch.setattr(placement, "AUTOLOAD_ENABLED", False)
    monkeypatch.setattr(placement, "reconcile_once", fake_reconcile)

    import auto_router.main as main_module

    admin_load = importlib.reload(main_module).admin_load
    state = SimpleNamespace()

    # apply omitted -> falls through to AUTOLOAD_ENABLED (false) -> dry run
    result = asyncio.run(admin_load())
    assert calls == [False]
    assert result == {"applied": False}

    # apply=true with the opt-in present -> realises the apply
    result = asyncio.run(admin_load(apply=True))
    assert calls == [False, True]
    assert result == {"applied": True}


def test_main_module_routes_all_carry_the_dependency() -> None:
    """Belt-and-braces check straight off the imported module, no subprocess."""

    from auto_router.main import app

    gated = {
        r.path
        for r in app.router.routes
        if isinstance(r, APIRoute)
        and any(
            getattr(d.call, "__name__", None) == "require_admin"
            for d in r.dependant.dependencies
        )
    }
    for required in ("/admin/load", "/v1/models", "/dashboard", "/admin/context"):
        assert required in gated, f"{required} lost its require_admin dependency"
