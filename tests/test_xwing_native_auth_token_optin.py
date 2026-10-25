"""Xwing native-auth client opt-in contract (offline; no provider HTTP).

Tests the *existing* OpenAI-compatible provider credential-file mechanism
against the future opt-in Xwing config, with no actual credentials.
"""
from pathlib import Path

import pytest

from auto_router.config import load_yaml
from auto_router.models import ProviderConfig
from auto_router.providers import OpenAICompatibleProvider, ProviderError


PROVIDERS = Path(__file__).resolve().parents[1] / "config" / "providers.yaml"
XWING = "lmstudio-xwing"
ENV = "LMSTUDIO_XWING_API_KEY_FILE"
FAKE = "synthetic-xwing-token-not-a-real-credential"


def _registry(monkeypatch, token_file: Path | None):
    if token_file is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, str(token_file))
    return load_yaml(PROVIDERS)["providers"]


def _xwing(monkeypatch, token_file: Path | None):
    matches = [item for item in _registry(monkeypatch, token_file)
               if item.get("name") == XWING]
    assert len(matches) == 1
    return ProviderConfig.model_validate(matches[0])


def test_default_remains_anonymous_until_controlled_migration(monkeypatch):
    cfg = _xwing(monkeypatch, None)
    assert cfg.enabled is True
    assert cfg.node_id == "xwing"
    assert cfg.api_key_file == ""
    assert cfg.api_key_env is None
    assert cfg.base_url.endswith("/v1")
    client = OpenAICompatibleProvider(cfg)
    assert client.is_configured()
    assert not any(k.casefold() == "authorization" for k in client._headers())
    # Existing default is backward-compatible, not proof of safe auth cutover.


def test_file_backed_bearer_is_in_memory_and_opt_in(monkeypatch, tmp_path):
    file = tmp_path / "owner-token"
    file.write_text(FAKE + "\n")
    file.chmod(0o600)
    cfg = _xwing(monkeypatch, file)
    assert cfg.api_key_file == str(file)
    client = OpenAICompatibleProvider(cfg)
    assert client.is_configured()
    headers = client._headers()
    assert "Authorization" in headers
    assert headers["Authorization"] == "Bearer " + FAKE
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
    cfg = _xwing(monkeypatch, path)
    client = OpenAICompatibleProvider(cfg)
    assert not client.is_configured()
    with pytest.raises(ProviderError, match="provider credentials unavailable"):
        client._headers()


def test_conflicting_env_and_file_refuses_token_selection(monkeypatch, tmp_path):
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


def test_no_other_provider_auto_receives_xwing_token_file(monkeypatch, tmp_path):
    path = tmp_path / "owner-token"
    path.write_text(FAKE)
    path.chmod(0o600)
    registry = _registry(monkeypatch, path)
    others = [p for p in registry if p["name"] != XWING]
    assert others
    assert all(p.get("api_key_file") != str(path) for p in others)
    assert sum(p.get("api_key_file") == str(path) for p in registry) == 1


def test_missing_token_file_never_triggers_http_client(monkeypatch, tmp_path):
    path = tmp_path / "nonexistent-owner-token"
    provider = OpenAICompatibleProvider(_xwing(monkeypatch, path))
    def explode():
        raise AssertionError("unexpected HTTP client creation")
    monkeypatch.setattr(provider, "_client", explode)
    assert not provider.is_configured()
    # The checked refusal is before any local or remote HTTP request.
