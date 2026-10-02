"""Configuration-authority contract tests.

These tests pin the housekeeping conclusions reached in
``docs/housekeeping/auto-router-config-and-dead-path-audit-2026-10-02.md``:

* ``archive/`` artifacts are unreachable from every configuration loader.
* A missing canonical context configuration degrades to a provider/agent-derived
  bootstrap snapshot and is labelled as such. It never resurrects archived static
  fleet state.
* The canonical provider registries validate under the strict-offline guard, while
  the archived hosted-provider registry is rejected by it.
* The production entrypoint stack (``secure_live``) mounts the Ed25519
  ``runtime_projection_v2`` manager, and ``main_live`` on its own mounts the
  schema-v1 HMAC manager.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import auto_router.runtime_projection as v1
import auto_router.runtime_projection_v2 as v2
from auto_router.config import (
    AgentWorkerRegistry,
    ProviderRegistry,
    load_agent_worker_registry,
    load_context_snapshot,
    load_policy_registry,
    load_provider_registry,
)
from auto_router.offline_guard import validate_offline_provider_config

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"
ARCHIVE_DIR = REPO_ROOT / "archive"

CANONICAL_PROVIDER_REGISTRIES = (
    "providers.yaml",
    "providers.reconciliation.yaml",
    "providers.example.yaml",
)


class _RecordingOpen:
    """Record every ``Path.open`` performed while loading configuration.

    ``config.load_yaml`` reads through ``Path.open`` (which delegates to
    ``io.open``, not ``builtins.open``), so patching the pathlib method observes
    every canonical loader read.
    """

    def __init__(self) -> None:
        self.paths: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = self

        def patched_open(path: Path, *args: Any, **kwargs: Any) -> io.IOBase:
            recorder.paths.append(str(path))
            return _original_path_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", patched_open)


_original_path_open = Path.open


def _load_all_canonical_config(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    recorder = _RecordingOpen()
    recorder.install(monkeypatch)
    try:
        load_provider_registry(CONFIG_DIR / "providers.yaml")
        load_policy_registry(CONFIG_DIR / "policies.yaml")
        load_agent_worker_registry(CONFIG_DIR / "agent_workers.yaml")
        load_context_snapshot(
            CONFIG_DIR / "context.yaml",
            ProviderRegistry.model_validate({"providers": []}),
            AgentWorkerRegistry(),
        )
    finally:
        monkeypatch.undo()
    return recorder.paths


def test_canonical_config_loading_never_opens_archived_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = _load_all_canonical_config(monkeypatch)

    assert opened, "expected the canonical loaders to read at least one file"
    archive_hits = [path for path in opened if "archive" in Path(path).parts]
    assert archive_hits == [], f"archived artifacts were opened: {archive_hits}"
    static_fallback_hits = [
        path
        for path in opened
        if path.endswith((".static-fallback", ".static-fallback.note", ".bak"))
    ]
    assert static_fallback_hits == [], f"archived fallbacks were opened: {static_fallback_hits}"


def test_archived_artifacts_exist_only_under_archive_directory() -> None:
    archived = sorted(path.name for path in ARCHIVE_DIR.iterdir() if path.is_file())
    assert "context.yaml.static-fallback" in archived
    assert "providers.yaml.bak" in archived

    # The same basenames must not be reachable from the canonical config directory,
    # where Settings resolves its cwd-relative defaults.
    canonical = sorted(path.name for path in CONFIG_DIR.iterdir() if path.is_file())
    assert "context.yaml" in canonical
    assert "providers.yaml" in canonical
    assert "context.yaml.static-fallback" not in canonical
    assert "providers.yaml.bak" not in canonical


def test_archived_hosted_provider_registry_is_rejected_by_offline_guard() -> None:
    errors = validate_offline_provider_config(ARCHIVE_DIR / "providers.yaml.bak", env={})

    assert errors, "the archived hosted-provider registry must not pass strict offline validation"
    joined = "; ".join(errors)
    assert "api.groq.com" in joined
    assert "quota_class must be 'local'" in joined


@pytest.mark.parametrize("name", CANONICAL_PROVIDER_REGISTRIES)
def test_canonical_provider_registries_pass_strict_offline_validation(name: str) -> None:
    assert validate_offline_provider_config(CONFIG_DIR / name, env={}) == []


def test_missing_canonical_context_degrades_to_labelled_bootstrap(tmp_path: Path) -> None:
    providers = ProviderRegistry.model_validate(
        {
            "providers": [
                {
                    "name": "lmstudio-local",
                    "type": "lmstudio",
                    "node_id": "local-node",
                    "enabled": True,
                    "base_url": "http://127.0.0.1:1234/v1",
                    "quota_class": "local",
                    "models": [
                        {
                            "alias": "local/default",
                            "provider_model": "local/default",
                            "capabilities": ["chat"],
                        }
                    ],
                }
            ]
        }
    )
    missing = tmp_path / "context.yaml"

    snapshot = load_context_snapshot(missing, providers, AgentWorkerRegistry())

    assert snapshot.revision == "bootstrap"
    assert snapshot.source == str(missing)
    assert snapshot.projection_status() == "bootstrap"
    assert snapshot.projection_error() == ""
    assert snapshot.metadata["projection_loaded"] is False
    assert snapshot.metadata["projection_source"] == str(missing)
    # Bootstrap context is derived from the provider registry, never from archive/.
    assert [provider.provider for provider in snapshot.providers] == ["lmstudio-local"]


_ENTRYPOINT_PROBE = """
import importlib, json, sys

entrypoint = sys.argv[1]
module = importlib.import_module(entrypoint)
main_live = importlib.import_module("auto_router.main_live")
v1 = importlib.import_module("auto_router.runtime_projection")
v2 = importlib.import_module("auto_router.runtime_projection_v2")

bound = main_live.RuntimeProjectionManager
print(json.dumps({
    "is_v1": bound is v1.RuntimeProjectionManager,
    "is_v2": bound is v2.RuntimeProjectionManager,
    "v2_subclasses_v1": issubclass(v2.RuntimeProjectionManager, v1.RuntimeProjectionManager),
    "v1_is_not_v2": not issubclass(v1.RuntimeProjectionManager, v2.RuntimeProjectionManager),
}))
"""


def _probe_entrypoint(entrypoint: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """Import one entrypoint in a fresh interpreter.

    ``secure_live`` rebinds ``main_live.RuntimeProjectionManager`` as an import-time
    side effect, so in-process assertions would depend on test collection order.
    A subprocess keeps each entrypoint's resolution independent.
    """

    monkeypatch.setenv("AUTO_ROUTER_STRICT_OFFLINE", "true")
    monkeypatch.setenv(
        "AUTO_ROUTER_PROVIDER_CONFIG",
        str(CONFIG_DIR / "providers.reconciliation.yaml"),
    )
    monkeypatch.setenv("AUTO_ROUTER_CONTEXT_CONFIG", str(CONFIG_DIR / "context.example.yaml"))
    monkeypatch.setenv("AUTO_ROUTER_DATABASE_URL", f"sqlite:///{tmp_path / 'router.sqlite3'}")
    result = subprocess.run(
        [sys.executable, "-c", _ENTRYPOINT_PROBE, entrypoint],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_main_live_entrypoint_resolves_schema_v1_projection_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _probe_entrypoint("auto_router.main_live", tmp_path, monkeypatch)

    assert probe["is_v1"] is True
    assert probe["is_v2"] is False
    assert probe["v1_is_not_v2"] is True


def test_secure_live_entrypoint_resolves_schema_v2_projection_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _probe_entrypoint("auto_router.secure_live", tmp_path, monkeypatch)

    # The production entrypoint (Dockerfile CMD) demands Ed25519 schema-v2.
    assert probe["is_v2"] is True
    assert probe["is_v1"] is False
    # v2 remains a strict subclass of v1: v1's swap mechanics are still live.
    assert probe["v2_subclasses_v1"] is True


def test_projection_managers_are_not_interchangeable() -> None:
    """v2 is a trust upgrade layered on v1, so neither implementation is dead."""

    assert issubclass(v2.RuntimeProjectionManager, v1.RuntimeProjectionManager)
    assert not issubclass(v1.RuntimeProjectionManager, v2.RuntimeProjectionManager)
