from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, HTTPException

from auto_router.context import ContextService
from auto_router.config import _project_live_models
from auto_router.live_models import LiveModelCache
from auto_router.model_registry import ModelRegistryStore
from auto_router.models import ProviderConfig
from auto_router.providers import build_provider
from auto_router.service_scanner import discover_tailnet_lmstudio_services, is_lmstudio_service
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
            "probe_summary": state.model_registry.probe_summary(),
            "provider_health": state.model_registry.provider_health_reports(),
            "recent_registry_snapshots": state.model_registry.recent_snapshots(limit=limit),
            "recent_provider_probes": state.model_registry.recent_probes(limit=limit),
        }

    @app.post("/admin/live-models/refresh")
    async def refresh_live_models(provider: str | None = None) -> dict[str, Any]:
        providers = selected_refresh_providers(state, provider)
        providers.extend(discovered_lmstudio_providers(state, provider))
        providers = dedupe_providers(providers)
        if provider and not providers:
            raise HTTPException(status_code=404, detail={"error": "provider not found or not enabled", "provider": provider})
        records = await refresh_provider_models(state, providers)
        return {
            "providers": records,
            "registry_summary": state.model_registry.summary(),
            "probe_summary": state.model_registry.probe_summary(),
            "tap_summary": _tap_summary(records),
        }

    @app.post("/admin/providers/probe")
    async def probe_provider_models(provider: str | None = None) -> dict[str, Any]:
        providers = selected_probe_providers(state, provider)
        providers.extend(discovered_lmstudio_providers(state, provider))
        providers = dedupe_providers(providers)
        if provider and not providers:
            raise HTTPException(status_code=404, detail={"error": "provider not found or not enabled", "provider": provider})
        records = await refresh_provider_models(state, providers)
        return {
            "providers": records,
            "registry_summary": state.model_registry.summary(),
            "probe_summary": state.model_registry.probe_summary(),
            "tap_summary": _tap_summary(records),
        }


def hydrate_live_models_from_registry(state: Any) -> None:
    for snapshot in state.model_registry.latest_inventory():
        state.live_models.put(snapshot)
    if hasattr(state, "context") and hasattr(state, "providers"):
        state.context = _project_live_models(state.context, state.providers, state.model_registry.latest_inventory())
        if hasattr(state, "policy_engine"):
            state.policy_engine.context = state.context


async def fetch_provider_models(provider: ProviderConfig) -> list[dict[str, Any]]:
    adapter = build_provider(provider, timeout_seconds=get_settings().request_timeout_seconds)
    return await adapter.list_models()


async def refresh_provider_models(state: Any, providers: list[ProviderConfig]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in providers:
        started = time.perf_counter()
        previous = state.model_registry.latest_for_provider(item.name)
        snapshot = await state.live_models.refresh_provider(item, fetch_provider_models)
        latency_ms = int((time.perf_counter() - started) * 1000)
        state.model_registry.save_snapshot(snapshot)
        probe = state.model_registry.save_probe(snapshot, latency_ms=latency_ms, previous_snapshot=previous)
        records.append(snapshot.to_dict() | {"probe": probe})
    hydrate_live_models_from_registry(state)
    return records


def discovered_lmstudio_providers(state: Any, provider_name: str | None = None) -> list[ProviderConfig]:
    candidates: list[ContextService] = []
    context = getattr(state, "context", None)
    if context is not None and hasattr(context, "all_services"):
        candidates.extend(service for service in context.all_services() if is_lmstudio_service(service))
    candidates.extend(discover_tailnet_lmstudio_services())

    providers_attr = getattr(state, "providers", None)
    enabled_known = providers_attr.enabled() if providers_attr and hasattr(providers_attr, "enabled") else []
    known = {provider.name for provider in getattr(providers_attr, "providers", []) or []}
    known.update(provider.name for provider in enabled_known)

    providers: list[ProviderConfig] = []
    for service in candidates:
        config = _provider_from_service(service)
        if provider_name and config.name != provider_name and service.service_id != provider_name:
            continue
        if config.name in known:
            continue
        providers.append(config)
    return providers


def dedupe_providers(providers: list[ProviderConfig]) -> list[ProviderConfig]:
    seen: set[str] = set()
    deduped: list[ProviderConfig] = []
    for provider in providers:
        key = provider.name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(provider)
    return deduped


def _provider_from_service(service: ContextService) -> ProviderConfig:
    base_url = _service_base_url(service)
    provider_name = service.provider or f"lmstudio-{service.node_id or service.service_id}".replace(".", "-")
    return ProviderConfig(
        name=provider_name,
        type="lmstudio",
        node_id=service.node_id,
        enabled=True,
        base_url=base_url,
        priority=service.priority,
        quota_class="local",
    )


def _service_base_url(service: ContextService) -> str:
    base = (service.url or service.health_url or "").rstrip("/")
    if base.endswith("/models"):
        return base[: -len("/models")]
    if base.endswith("/api/v1/models"):
        return base[: -len("/models")]
    return base


def selected_refresh_providers(state: Any, provider_name: str | None = None) -> list[ProviderConfig]:
    configured = getattr(state.providers, "enabled", lambda: [])()
    if provider_name is None:
        return [provider for provider in configured if provider.type != "lmstudio"]
    return [provider for provider in configured if provider.name == provider_name]


def selected_probe_providers(state: Any, provider_name: str | None = None) -> list[ProviderConfig]:
    configured = getattr(state.providers, "enabled", lambda: [])()
    if provider_name is None:
        return list(configured)
    return [provider for provider in configured if provider.name == provider_name]


def refreshable_providers(providers: list[ProviderConfig], provider_name: str | None = None) -> list[ProviderConfig]:
    items = [provider for provider in providers if provider.enabled and provider.type != "lmstudio"]
    if provider_name is None:
        return items
    return [provider for provider in items if provider.name == provider_name]


def probeable_providers(providers: list[ProviderConfig], provider_name: str | None = None) -> list[ProviderConfig]:
    items = [provider for provider in providers if provider.enabled]
    if provider_name is None:
        return items
    return [provider for provider in items if provider.name == provider_name]


def _tap_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "providers": len(records),
        "ok": sum(1 for record in records if record.get("ok")),
        "error": sum(1 for record in records if not record.get("ok")),
        "models": sum(int(record.get("model_count") or 0) for record in records),
        "drift": sum(1 for record in records if record.get("probe", {}).get("drift")),
    }
