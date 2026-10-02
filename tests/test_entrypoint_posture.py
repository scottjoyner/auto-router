"""Contract tests for the shipped entrypoint posture.

`auto-router` used to install and run `auto_router.main_live:app`, which omits
claim-scoped executor authentication, durable request idempotency, iterator-final
streaming accounting, and the schema-v2 Ed25519 runtime projection. Installing the
package and running the console script therefore produced a materially weaker
posture than `docker compose up` on the same source tree -- and the README
quickstart said to do exactly that.

`auto_router.main_live:app` stays importable so the schema-v1 HMAC migration path
remains testable. It is simply no longer what anything defaults to.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURE_ENTRYPOINT = "auto_router.secure_live"


def test_dockerfile_cmd_runs_secure_live() -> None:
    text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    cmd = re.search(r'^CMD\s+(.*)$', text, re.M)
    assert cmd is not None, "Dockerfile must declare a CMD"
    assert f"{SECURE_ENTRYPOINT}:app" in cmd.group(1), cmd.group(1)


def test_console_script_runs_secure_live() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts["auto-router"] == f"{SECURE_ENTRYPOINT}:run", scripts


def test_make_dev_runs_secure_live() -> None:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    dev = re.search(r"^dev:\n((?:\t.*\n)+)", text, re.M)
    assert dev is not None, "Makefile must keep a dev target"
    body = dev.group(1)
    assert f"uvicorn {SECURE_ENTRYPOINT}:app" in body, body
    assert "main_live:app" not in body, "dev must not run the weaker entrypoint"


def test_readme_quickstart_does_not_prescribe_main_live() -> None:
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "auto_router.main_live:app" not in text
    assert "auto_router.main_live:run" not in text


_PROBE = """
import importlib
import json
import sys

module = importlib.import_module(sys.argv[1])
app = module.app
print(json.dumps({
    "module": sys.argv[1],
    "user_middleware": len(app.user_middleware),
    "executor_auth": bool(getattr(app.state, "executor_inference_auth_installed", False)),
    "has_run": callable(getattr(module, "run", None)),
    "run_exported": "run" in getattr(module, "__all__", []),
}))
"""


def _probe_entrypoint(module_name: str, tmp_path_factory) -> dict:
    """Import exactly one entrypoint in a fresh interpreter.

    Both entrypoints share one ``app`` object: ``secure_live`` imports
    ``main_live`` and installs its middleware onto that app. Probing them in the
    same process would report ``main_live`` as already-hardened and hide exactly
    the difference this contract depends on.
    """

    import json
    import os

    env = dict(os.environ)
    env.update(
        AUTO_ROUTER_STRICT_OFFLINE="true",
        AUTO_ROUTER_PROVIDER_CONFIG=str(
            REPO_ROOT / "config" / "providers.reconciliation.yaml"
        ),
        AUTO_ROUTER_CONTEXT_CONFIG=str(REPO_ROOT / "config" / "context.example.yaml"),
        AUTO_ROUTER_ADMIN_TOKEN="entrypoint-test-token",
        AUTO_ROUTER_DATABASE_URL=f"sqlite:///{tmp_path_factory.mktemp('probe') / 'r.sqlite3'}",
    )
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, module_name],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def secure_entrypoint(tmp_path_factory) -> dict:
    return _probe_entrypoint("auto_router.secure_live", tmp_path_factory)


@pytest.fixture(scope="module")
def main_live_entrypoint(tmp_path_factory) -> dict:
    return _probe_entrypoint("auto_router.main_live", tmp_path_factory)


def test_secure_live_exposes_a_run_entrypoint(secure_entrypoint) -> None:
    assert secure_entrypoint["has_run"] is True
    assert secure_entrypoint["run_exported"] is True


def test_secure_live_installs_executor_inference_auth(secure_entrypoint) -> None:
    assert secure_entrypoint["executor_auth"] is True


def test_bare_main_live_lacks_executor_inference_auth(
    secure_entrypoint,
    main_live_entrypoint,
) -> None:
    """Documents *why* nothing defaults to main_live any more.

    If this ever flips to True the difference the entrypoint fix relies on has
    disappeared and the contract above should be re-examined.
    """

    assert main_live_entrypoint["executor_auth"] is False
    assert (
        secure_entrypoint["user_middleware"] > main_live_entrypoint["user_middleware"]
    )
