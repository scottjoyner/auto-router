from __future__ import annotations

import asyncio
from types import SimpleNamespace

import auto_router.live_model_routes as live_model_routes
from auto_router.live_models import LiveModelCache
from auto_router.models import ProviderConfig


class _ReadOnlyModelRegistry:
    def __init__(self) -> None:
        self.saved = []

    def latest_for_provider(self, provider: str):
        return None

    def save_snapshot(self, snapshot) -> None:
        raise OSError("attempt to write a readonly database")

    def save_probe(self, snapshot, latency_ms=None, previous_snapshot=None):
        raise OSError("attempt to write a readonly database")


async def _fake_fetch_provider_models(provider: ProviderConfig):
    return [{"id": f"{provider.name}.model-1", "name": "Model One"}]


def test_refresh_provider_models_survives_registry_write_failures(monkeypatch) -> None:
    provider = ProviderConfig(
        name="lmstudio-xwing",
        type="lmstudio",
        base_url="http://127.0.0.1:1234/v1",
        enabled=True,
        quota_class="local",
    )
    state = SimpleNamespace(
        live_models=LiveModelCache(ttl_seconds=300),
        model_registry=_ReadOnlyModelRegistry(),
        signal_registry=SimpleNamespace(save_snapshot=lambda snapshot: None),
    )
    monkeypatch.setattr(live_model_routes, "fetch_provider_models", _fake_fetch_provider_models)

    records = asyncio.run(live_model_routes.refresh_provider_models(state, [provider]))

    assert len(records) == 1
    record = records[0]
    assert record["provider"] == "lmstudio-xwing"
    assert record["ok"] is True
    assert record["model_count"] == 1
    assert record["registry_error"] == "attempt to write a readonly database"
    assert record["probe"]["error"] == "attempt to write a readonly database"
