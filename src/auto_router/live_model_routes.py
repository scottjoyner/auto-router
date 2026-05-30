from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from auto_router.live_models import LiveModelCache
from auto_router.model_registry import ModelRegistryStore
from auto_router.models import ProviderConfig
from auto_router.providers import build_provider
from auto_router.settings import get_settings


def register_live_model_routes(app: FastAPI, state: Any) -> None:
    if not hasattr(state, "live_models"):
        state.live_models = LiveModelCache(ttl_seconds=get_settings().live_model_cache_ttl_seconds)
    if not hasattr(state, "model_registry"):
        state.model_registry = ModelRegistryStore(get_settings().database_url)
        hydrate_live_models_from_registry(state)

    @app.get("/admin/live-models")
    async def admin_live_models(limit: int = 100) -> dict[str, Any]:
        return {
            "providers": state.live_models.snapshot(),
            "registry_summary": state.model_registry.summary(),
            "recent_registry_snapshots": state.model_registry.recent_snapshots(limit=limit),
        }

    @app.post("/admin/live-models/refresh")
    async def refresh_live_models(provider: str | None = None) -> dict[str, Any]:
        providers = refreshable_providers(state.providers.enabled(), provider)
        if provider and not providers:
            raise HTTPException(status_code=404, detail={"error": "provider not found or not enabled", "provider": provider})
        records = []
        for item in providers:
            snapshot = await state.live_models.refresh_provider(item, fetch_provider_models)
            state.model_registry.save_snapshot(snapshot)
            records.append(snapshot.to_dict())
        return {
            "providers": records,
            "registry_summary": state.model_registry.summary(),
        }


def hydrate_live_models_from_registry(state: Any) -> None:
    for snapshot in state.model_registry.latest_snapshots():
        state.live_models.put(snapshot)


async def fetch_provider_models(provider: ProviderConfig) -> list[dict[str, Any]]:
    adapter = build_provider(provider, timeout_seconds=get_settings().request_timeout_seconds)
    return await adapter.list_models()


def refreshable_providers(providers: list[ProviderConfig], provider_name: str | None = None) -> list[ProviderConfig]:
    items = [provider for provider in providers if provider.enabled and provider.type != "lmstudio"]
    if provider_name is None:
        return items
    return [provider for provider in items if provider.name == provider_name]
