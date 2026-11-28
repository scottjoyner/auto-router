"""Deny-only bearer migration tests for the real auto-router provider.

No network, real tokens, resident model requests, or fleet routing.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from auto_router.models import ProviderConfig
from auto_router.provider_credential_file import ProviderCredentialHold, read_provider_token_file
from auto_router.providers import OpenAICompatibleProvider, ProviderError

FAKE = "synthetic-router-only-credential-20261024"


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    p = tmp_path / "model-provider-token"
    p.write_text(FAKE)
    p.chmod(0o600)
    return p


def client(token_file: Path | None = None, api_key_env: str | None = None) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        ProviderConfig(
            name="xwing-model-gateway",
            type="lmstudio",
            node_id="xwing",
            base_url="http://xwing.tailcb8954.ts.net:1234/v1",
            api_key_file=str(token_file) if token_file else None,
            api_key_env=api_key_env,
        )
    )


def test_owner_only_token_file(token_file: Path):
    assert read_provider_token_file(str(token_file)) == FAKE


def test_token_is_not_stored_in_provider_config(token_file: Path):
    provider = client(token_file)
    assert provider.config.api_key_file == str(token_file)
    assert FAKE not in provider.config.model_dump_json()


def test_bearer_applies_only_to_selected_provider(token_file: Path):
    provider = client(token_file)
    assert provider._headers()["Authorization"] == "Bearer " + FAKE
    legacy = client()
    assert "Authorization" not in legacy._headers()


def test_missing_token_marked_unconfigured(tmp_path: Path):
    p = client(tmp_path / "absent-token")
    assert p.is_configured() is False
    assert "Authorization" not in client()._headers()
    with pytest.raises(ProviderError, match="credentials unavailable"):
        p._headers()


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o666])
def test_group_or_world_readable_refused(token_file: Path, mode: int):
    token_file.chmod(mode)
    assert client(token_file).is_configured() is False
    with pytest.raises(ProviderCredentialHold):
        read_provider_token_file(str(token_file))


def test_symlink_credential_refused(token_file: Path):
    link = token_file.parent / "symlink"
    link.symlink_to(token_file)
    assert client(link).is_configured() is False


def test_relative_path_refused():
    with pytest.raises(ProviderCredentialHold, match="absolute"):
        read_provider_token_file("token")


@pytest.mark.parametrize("contents", [
    "short",
    "synthetic-with-space prohibited",
    "synthetic\nAuthorization: malicious",
    "a" * 2100,
])
def test_bad_credential_data_refused(token_file: Path, contents: str):
    token_file.write_text(contents)
    assert client(token_file).is_configured() is False


def test_configured_env_and_file_conflict(token_file: Path, monkeypatch):
    monkeypatch.setenv("MODELGATEWAY_KEY", "synthetic-env")
    provider = client(token_file, api_key_env="MODELGATEWAY_KEY")
    assert provider.is_configured() is False
    with pytest.raises(ProviderError, match="conflicting"):
        provider._headers()


def test_configured_header_and_file_conflict(token_file: Path):
    config = ProviderConfig(
        name="xwing", type="lmstudio", base_url="http://127.0.0.1:1234/v1",
        api_key_file=str(token_file),
        headers={"authorization": "Bearer forged"},
    )
    provider = OpenAICompatibleProvider(config)
    with pytest.raises(ProviderError, match="conflicting"):
        provider._headers()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 401, 403, 404, 429, 500, 503])
async def test_health_does_not_admit_denial_or_redirect(
    token_file: Path, monkeypatch, status: int
):
    provider = client(token_file)
    native_async_client = httpx.AsyncClient
    reqs = []
    def mock_response(request):
        reqs.append(request)
        return httpx.Response(status, json={"error": "synthetic"})
    transport = httpx.MockTransport(mock_response)
    monkeypatch.setattr(
        "auto_router.providers.httpx.AsyncClient",
        lambda **kwargs: native_async_client(transport=transport, **kwargs),
    )
    health = await provider.health()
    assert health.ok is False
    assert health.detail == f"HTTP {status}"
    assert len(reqs) == 1
    assert reqs[0].headers["Authorization"] == "Bearer " + FAKE


@pytest.mark.asyncio
async def test_health_authorized_200(token_file: Path, monkeypatch):
    native_async_client = httpx.AsyncClient
    seen = []
    def mock_response(request):
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"data": [{"id": "synthetic"}]})
    transport = httpx.MockTransport(mock_response)
    monkeypatch.setattr(
        "auto_router.providers.httpx.AsyncClient",
        lambda **kwargs: native_async_client(transport=transport, **kwargs),
    )
    health = await client(token_file).health()
    assert health.ok is True
    assert seen == ["Bearer " + FAKE]


@pytest.mark.asyncio
async def test_health_missing_secret_does_not_open_network(tmp_path: Path, monkeypatch):
    provider = client(tmp_path / "unavailable")
    monkeypatch.setattr(
        "auto_router.providers.httpx.AsyncClient",
        lambda **kwargs: pytest.fail("network must not be invoked"),
    )
    result = await provider.health()
    assert result.ok is False
    assert "missing API key" in result.detail


def test_existing_lmstudio_without_secret_retains_legacy_behavior():
    p = client()
    assert p.is_configured() is True
    assert "Authorization" not in p._headers()
