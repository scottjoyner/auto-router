"""Xwing provider file-token opt-in regression; offline and runtime-isolated.

Import the *real* provider/models/credential modules through a temporary
package namespace, avoiding production auto_router.__init__ side effects.
Use the actual _expand_env function extracted from its source AST, rather
than reimplementing interpolation. No application service or provider HTTP.
"""
import ast
import importlib.util
import os
from pathlib import Path
import re
import sys
import types
from typing import Any
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/auto_router"
PROVIDERS = ROOT / "config/providers.yaml"
XWING = "lmstudio-xwing"
ENV = "LMSTUDIO_XWING_API_KEY_FILE"
FAKE = "synthetic-xwing-token-not-a-real-credential"


def _real_modules_without_runtime_bootstrap():
    """Load unchanged repo modules without executing fleet startup policy."""
    package = types.ModuleType("auto_router")
    package.__path__ = [str(SOURCE)]
    package.__package__ = "auto_router"
    instances = {}
    with patch.dict(sys.modules, {"auto_router": package}):
        for name in ("models", "provider_credential_file", "providers"):
            fullname = "auto_router." + name
            spec = importlib.util.spec_from_file_location(fullname, SOURCE / (name + ".py"))
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[fullname] = module
            spec.loader.exec_module(module)
            instances[name] = module
    return (
        instances["models"].ProviderConfig,
        instances["providers"].OpenAICompatibleProvider,
        instances["providers"].ProviderError,
    )


ProviderConfig, OpenAICompatibleProvider, ProviderError = _real_modules_without_runtime_bootstrap()


def _exact_config_env_expander():
    """Compile only actual repository _expand_env and _ENV_PATTERN AST nodes."""
    tree = ast.parse((SOURCE / "config.py").read_text(encoding="utf-8"))
    code = []
    for item in tree.body:
        if isinstance(item, ast.FunctionDef) and item.name == "_expand_env":
            code.append(item)
        elif isinstance(item, ast.Assign) and any(
            isinstance(k, ast.Name) and k.id == "_ENV_PATTERN" for k in item.targets
        ):
            code.append(item)
    assert len(code) == 2, "unexpected actual config-expansion contract"
    ns = {"os": os, "re": re, "Any": Any}
    exec(compile(ast.Module(body=code, type_ignores=[]), str(SOURCE / "config.py"), "exec"), ns)
    return ns["_expand_env"]


_expand_env = _exact_config_env_expander()


def _registry(monkeypatch, token_file: Path | None):
    if token_file is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, str(token_file))
    raw = yaml.safe_load(PROVIDERS.read_text(encoding="utf-8"))
    return _expand_env(raw)["providers"]


def _xwing(monkeypatch, token_file: Path | None):
    matches = [item for item in _registry(monkeypatch, token_file)
               if item.get("name") == XWING]
    assert len(matches) == 1
    return ProviderConfig.model_validate(matches[0])


def test_default_remains_unchanged_until_auth_migration(monkeypatch):
    cfg = _xwing(monkeypatch, None)
    assert cfg.enabled is True
    assert cfg.node_id == "xwing"
    assert cfg.api_key_file == ""
    assert cfg.api_key_env is None
    assert cfg.base_url.endswith("/v1")
    client = OpenAICompatibleProvider(cfg)
    assert client.is_configured()
    assert not any(k.casefold() == "authorization" for k in client._headers())
    # This is compatibility proof, NOT catalog authentication proof.


def test_valid_owner_file_is_selected_only_after_opt_in(monkeypatch, tmp_path):
    file = tmp_path / "owner-token"
    file.write_text(FAKE + "\n")
    file.chmod(0o600)
    cfg = _xwing(monkeypatch, file)
    assert cfg.api_key_file == str(file)
    client = OpenAICompatibleProvider(cfg)
    assert client.is_configured()
    assert client._headers()["Authorization"] == "Bearer " + FAKE
    assert cfg.api_key_env is None
    assert FAKE not in cfg.model_dump_json()


@pytest.mark.parametrize("kind", ["missing", "world_readable", "symlink", "directory"])
def test_unsafe_configured_token_does_not_fallback_to_anonymous(
    monkeypatch, tmp_path, kind,
):
    path = tmp_path / "owner-token"
    if kind == "world_readable":
        path.write_text(FAKE)
        path.chmod(0o644)
    elif kind == "symlink":
        target = tmp_path / "target-token"
        target.write_text(FAKE)
        target.chmod(0o600)
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    provider = OpenAICompatibleProvider(_xwing(monkeypatch, path))
    assert not provider.is_configured()
    with pytest.raises(ProviderError, match="provider credentials unavailable"):
        provider._headers()


def test_conflicting_env_and_file_refuses_selection(monkeypatch, tmp_path):
    token = tmp_path / "owner-token"
    token.write_text(FAKE)
    token.chmod(0o600)
    cfg = _xwing(monkeypatch, token)
    cfg.api_key_env = "SYNTHETIC_CONFLICT_TOKEN"
    monkeypatch.setenv("SYNTHETIC_CONFLICT_TOKEN", FAKE)
    client = OpenAICompatibleProvider(cfg)
    assert not client.is_configured()
    with pytest.raises(ProviderError, match="conflicting provider token sources"):
        client._headers()


def test_opt_in_not_applied_to_other_nodes(monkeypatch, tmp_path):
    path = tmp_path / "owner-token"
    path.write_text(FAKE)
    path.chmod(0o600)
    registry = _registry(monkeypatch, path)
    others = [p for p in registry if p["name"] != XWING]
    assert others
    assert all(p.get("api_key_file") != str(path) for p in others)
    assert sum(p.get("api_key_file") == str(path) for p in registry) == 1


def test_missing_token_refuses_before_any_http(monkeypatch, tmp_path):
    path = tmp_path / "nonexistent-owner-token"
    provider = OpenAICompatibleProvider(_xwing(monkeypatch, path))
    def explode():
        raise AssertionError("unexpected HTTP client creation")
    monkeypatch.setattr(provider, "_client", explode)
    assert not provider.is_configured()
    with pytest.raises(ProviderError):
        provider._headers()
