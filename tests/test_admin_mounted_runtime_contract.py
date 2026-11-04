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
    [("main", 13), ("main_live", 19)],
)
def test_all_mounted_admin_routes_require_auth_before_execution(entrypoint,expected_count):
    env = os.environ.copy()
    env["AUTO_ROUTER_STRICT_OFFLINE"] = "true"
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
