"""Readiness must fail closed on unauthenticated/rejected GPU backends."""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the standalone router modules without invoking optional server settings
# and integrations in auto_router.__init__.
SOURCE = Path(__file__).resolve().parents[1] / "src" / "auto_router"


def _modules():
    pkg = types.ModuleType("auto_router")
    pkg.__path__ = [str(SOURCE)]
    with patch.dict(sys.modules, {"auto_router": pkg}):
        model_mod = importlib.import_module("auto_router.models")
        provider_mod = importlib.import_module("auto_router.providers")
    return model_mod, provider_mod


@pytest.mark.parametrize("http_status,expected_healthy", [
    (200, True),
    (201, False),
    (204, False),
    (301, False),
    (302, False),
    (401, False),
    (403, False),
    (404, False),
    (408, False),
    (429, False),
    (500, False),
    (503, False),
])
def test_auth_rejection_never_counts_as_healthy(monkeypatch, http_status, expected_healthy):
    models, providers = _modules()
    monkeypatch.setenv("FLEET_GPU_TEST_KEY", "fixture-not-secret")
    candidate = models.ProviderConfig(
        name="isolated-r9700-health-smoke",
        type="openai_compatible",
        base_url="http://100.83.215.83:18321/v1",
        api_key_env="FLEET_GPU_TEST_KEY",
    )
    provider = providers.OpenAICompatibleProvider(candidate)
    seen = []

    class StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, url, headers):
            seen.append((url, dict(headers)))
            return types.SimpleNamespace(status_code=http_status)

    with patch.object(providers.httpx, "AsyncClient", return_value=StubClient()):
        result = asyncio.run(provider.health())
    assert result.ok is expected_healthy
    assert result.detail == f"HTTP {http_status}"
    assert len(seen) == 1
    assert seen[0][0] == candidate.base_url + "/models"
    assert seen[0][1]["Authorization"] == "Bearer fixture-not-secret"


def test_missing_key_never_attempts_model_catalog(monkeypatch):
    models, providers = _modules()
    monkeypatch.delenv("FLEET_GPU_TEST_MISSING", raising=False)
    candidate = models.ProviderConfig(
        name="no-key-gpu", type="openai_compatible",
        base_url="http://100.83.215.83:18321/v1",
        api_key_env="FLEET_GPU_TEST_MISSING",
    )
    with patch.object(providers.httpx, "AsyncClient") as client:
        result = asyncio.run(providers.OpenAICompatibleProvider(candidate).health())
    assert result.ok is False
    assert result.detail == "missing API key"
    client.assert_not_called()
