from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import FastAPI, HTTPException

from auto_router.context import ContextService
from auto_router.config import _project_live_models
from auto_router.live_models import LiveModelCache
from auto_router.model_registry import ModelRegistryStore
from auto_router.models import ModelConfig, ProviderConfig
from auto_router.providers import build_provider
from auto_router.service_scanner import discover_tailnet_lmstudio_services, is_lmstudio_service
from auto_router.settings import get_settings
from auto_router.signal_registry import live_model_signals, signal_snapshot


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
        await merge_discovered_lmstudio_providers(state, provider)
        providers = selected_refresh_providers(state, provider)
        if provider is None:
            providers.extend(item for item in state.providers.enabled() if item.type == "lmstudio")
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
        await merge_discovered_lmstudio_providers(state, provider)
        providers = selected_probe_providers(state, provider)
        if provider is None:
            providers.extend(item for item in state.providers.enabled() if item.type == "lmstudio")
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
        if hasattr(state, "signal_registry"):
            state.context = state.signal_registry.hydrate_context(state.context)
        if hasattr(state, "policy_engine"):
            state.policy_engine.context = state.context


async def fetch_provider_models(provider: ProviderConfig) -> list[dict[str, Any]]:
    adapter = build_provider(provider, timeout_seconds=get_settings().request_timeout_seconds)
    return await adapter.list_models()


# Tracks the last known up/down state per provider so the continuous poll loop
# only re-projects the live models into the routing context when something
# actually changed (a model set drifted, or a provider came up/went down).
_LIVE_OK_STATE: dict[str, bool] = {}
_LIVE_SIG_STATE: dict[str, str | None] = {}


async def refresh_provider_models(state: Any, providers: list[ProviderConfig]) -> list[dict[str, Any]]:
    """Fetch /v1/models for every provider concurrently and fold the results into
    the live-model cache + registry. Re-projects the live models into the routing
    context only when something actually changed, so a continuous poll loop stays
    cheap while still propagating model swaps (e.g. a node loading a medium/large
    model in place of its 3B) into routing instantly.

    The sqlite registry is only written when a provider's up/down state or model
    signature actually changes -- a stable fleet otherwise just updates the
    in-memory cache, avoiding a blocking DB write every poll cycle."""

    async def _refresh_one(item: ProviderConfig) -> dict[str, Any]:
        started = time.perf_counter()
        snapshot = await state.live_models.refresh_provider(item, fetch_provider_models)
        latency_ms = int((time.perf_counter() - started) * 1000)
        provider = snapshot.provider
        ok = bool(snapshot.ok)
        prior_ok = _LIVE_OK_STATE.get(provider)
        signature = (
            state.model_registry._snapshot_signature(snapshot.models)
            if hasattr(state, "model_registry")
            else None
        )
        prior_sig = _LIVE_SIG_STATE.get(provider)
        changed = (
            prior_ok is None
            or prior_ok != ok
            or (ok and prior_sig is not None and prior_sig != signature)
        )
        _LIVE_OK_STATE[provider] = ok
        _LIVE_SIG_STATE[provider] = signature
        registry_error: str | None = None
        probe: dict[str, Any] | None = None
        if changed and hasattr(state, "model_registry"):
            try:
                previous = await asyncio.to_thread(state.model_registry.latest_for_provider, provider)
                await asyncio.to_thread(state.model_registry.save_snapshot, snapshot)
                probe = await asyncio.to_thread(
                    lambda: state.model_registry.save_probe(snapshot, latency_ms=latency_ms, previous_snapshot=previous)
                )
            except Exception as exc:
                registry_error = str(exc)
        if probe is None:
            probe = {
                "provider": provider,
                "ok": ok,
                "fetched_at": snapshot.fetched_at,
                "expires_at": snapshot.expires_at,
                "latency_ms": latency_ms,
                "model_count": len(snapshot.models),
                "drift": False,
                "signature": signature,
                "previous_signature": prior_sig,
                "error": snapshot.error,
                "models": snapshot.models,
            }
        if hasattr(state, "signal_registry"):
            try:
                signals = live_model_signals(snapshot, node_id=item.node_id)
                sig = signal_snapshot(signals, revision=f"live-model:{item.name}", source="live_models")
                await asyncio.to_thread(state.signal_registry.save_snapshot, sig)
            except Exception:
                pass
        record = snapshot.to_dict() | {"probe": probe, "drift": probe.get("drift")}
        if registry_error:
            record["registry_error"] = registry_error
        return record

    records = await asyncio.gather(*(_refresh_one(item) for item in providers))
    changed = False
    for rec in records:
        provider = rec.get("provider")
        now_ok = bool(rec.get("ok", True))
        prior = _LIVE_OK_STATE.get(provider)
        if rec.get("drift") or prior is None or prior != now_ok:
            changed = True
        _LIVE_OK_STATE[provider] = now_ok
    if changed:
        try:
            hydrate_live_models_from_registry(state)
        except Exception:
            pass
    # Fold the live LM Studio model list into the provider registry that /v1/models
    # + routing read from. This is what makes a newly-loaded model (e.g. a node
    # swapping its 3B for a 35B) instantly routable, and an unloaded model instantly
    # dropped -- the live model list is the source of truth for what can serve.
    try:
        synced = sync_live_models_to_providers(state)
        if synced:
            print(f"[live-poll] synced {synced} provider model set(s) into routing")
    except Exception as exc:
        print(f"[live-poll] sync_live_models_to_providers error: {exc}")
    return records


def _infer_capabilities(model_id: str, metadata: dict) -> set[str]:
    mid = (model_id or "").lower()
    mtype = str(metadata.get("type") or "").lower()
    if "embed" in mid or mtype == "embedding":
        return {"embed"}
    arch = str(metadata.get("architecture") or "").lower()
    if "vision" in mid or "vl" in mid or "vision" in arch:
        return {"vision", "chat", "completion"}
    return {"chat", "completion"}


def _is_model_loaded(entry: Any) -> bool:
    """A model is resident iff LM Studio reports loaded instances. The top-level
    ``loaded`` boolean is unreliable; the authoritative signal is
    ``raw.loaded_instances`` (non-empty = actually running on the endpoint)."""
    if not isinstance(entry, dict):
        return False
    raw = entry.get("raw", {}) or {}
    instances = raw.get("loaded_instances") or entry.get("loaded_instances") or []
    if isinstance(instances, list) and instances:
        return True
    return bool(entry.get("loaded"))


def sync_live_models_to_providers(state: Any) -> int:
    """Write the live-discovered model list for each lmstudio provider into
    ``state.providers`` (which /v1/models + request routing consume). Returns the
    number of providers whose model set changed."""
    registry = getattr(state, "providers", None)
    live = getattr(state, "live_models", None)
    if registry is None or live is None or not hasattr(registry, "providers"):
        return 0
    changed = 0
    providers = list(registry.providers)
    for idx, provider in enumerate(providers):
        if provider.type != "lmstudio":
            continue
        snap = live.get(provider.name) or live.get(provider.id)
        if snap is None or not snap.ok:
            continue
        static = {m.provider_model: m for m in provider.models}
        new_models: list[ModelConfig] = []
        for entry in snap.models:
            # Only advertise models that are actually resident on the endpoint.
            # LM Studio's model list includes the whole downloaded catalog, so
            # advertising unloaded models would route requests to empty endpoints
            # (HTTP 400/500) and trip circuit breakers. Skip anything whose
            # loaded_instances list is empty.
            if not _is_model_loaded(entry):
                continue
            mid = entry.get("id") if isinstance(entry, dict) else str(entry)
            if not mid:
                continue
            meta = entry.get("metadata", {}) if isinstance(entry, dict) else {}
            ctx = meta.get("context_length") or meta.get("context_window")
            static_model = static.get(mid)
            caps = set(static_model.capabilities) if (static_model and static_model.capabilities) else _infer_capabilities(mid, meta)
            cw = int(ctx) if ctx else (static_model.context_window if static_model else None)
            new_models.append(ModelConfig(alias=mid, provider_model=mid, capabilities=caps, context_window=cw))
        if [m.provider_model for m in new_models] != [m.provider_model for m in provider.models]:
            providers[idx] = provider.model_copy(update={"models": new_models})
            changed += 1
    if changed:
        registry.providers = providers
    return changed


async def discovered_lmstudio_providers(state: Any, provider_name: str | None = None, include_known: bool = False) -> list[ProviderConfig]:
    candidates: list[ContextService] = []
    context = getattr(state, "context", None)
    if context is not None and hasattr(context, "all_services"):
        candidates.extend(service for service in context.all_services() if is_lmstudio_service(service))
    # `tailscale status` is a blocking subprocess -- run it off the event loop so
    # the 5s discovery poll can never freeze request handling.
    candidates.extend(await asyncio.to_thread(discover_tailnet_lmstudio_services))

    providers_attr = getattr(state, "providers", None)
    enabled_known = providers_attr.enabled() if providers_attr and hasattr(providers_attr, "enabled") else []
    known = set()
    if not include_known:
        known = {provider.name.strip().lower() for provider in getattr(providers_attr, "providers", []) or []}
        known.update(provider.name.strip().lower() for provider in enabled_known)
        known.update(
            getattr(context, "canonical_provider_name", lambda value: str(value).strip().lower())(
                getattr(provider, "provider", getattr(provider, "name", ""))
            )
            for provider in getattr(context, "providers", []) or []
        )

    target_provider = None
    if provider_name:
        target_provider = getattr(context, "canonical_provider_name", lambda value: str(value).strip().lower())(provider_name)

    providers: list[ProviderConfig] = []
    for service in candidates:
        config = _provider_from_service(service)
        config_name = config.name.strip().lower()
        service_name = service.service_id.strip().lower()
        if target_provider and config_name != target_provider and service_name != target_provider:
            continue
        if not include_known and config_name in known:
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


async def merge_discovered_lmstudio_providers(state: Any, provider_name: str | None = None) -> int:
    discovered = dedupe_providers(await discovered_lmstudio_providers(state, provider_name, include_known=True))
    if not discovered:
        return 0
    registry = getattr(state, "providers", None)
    if registry is None or not hasattr(registry, "providers"):
        return 0
    providers = list(registry.providers)
    index_by_name = {provider.name.strip().lower(): idx for idx, provider in enumerate(providers)}
    changed = 0
    for provider in discovered:
        key = provider.name.strip().lower()
        if key in index_by_name:
            idx = index_by_name[key]
            current = providers[idx]
            providers[idx] = current.model_copy(
                update={
                    "enabled": bool(current.enabled or provider.enabled),
                    "base_url": provider.base_url or current.base_url,
                    "node_id": provider.node_id or current.node_id,
                    "priority": min(getattr(current, "priority", 100), getattr(provider, "priority", 100)),
                }
            )
        else:
            providers.append(provider)
            index_by_name[key] = len(providers) - 1
        changed += 1
    registry.providers = providers
    return changed


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
    providers_attr = getattr(state, "providers", None)
    configured = providers_attr.enabled() if providers_attr and hasattr(providers_attr, "enabled") else []
    context = getattr(state, "context", None)
    target = getattr(context, "canonical_provider_name", lambda value: str(value).strip().lower())(provider_name) if provider_name else None
    if provider_name is None:
        return [provider for provider in configured if provider.type != "lmstudio"]
    return [provider for provider in configured if getattr(context, "canonical_provider_name", lambda value: str(value).strip().lower())(provider.name) == target]


def selected_probe_providers(state: Any, provider_name: str | None = None) -> list[ProviderConfig]:
    configured = getattr(state.providers, "enabled", lambda: [])()
    context = getattr(state, "context", None)
    target = getattr(context, "canonical_provider_name", lambda value: str(value).strip().lower())(provider_name) if provider_name else None
    if provider_name is None:
        return list(configured)
    return [provider for provider in configured if getattr(context, "canonical_provider_name", lambda value: str(value).strip().lower())(provider.name) == target]


def refreshable_providers(providers: list[ProviderConfig], provider_name: str | None = None) -> list[ProviderConfig]:
    items = [provider for provider in providers if provider.enabled and provider.type != "lmstudio"]
    if provider_name is None:
        return items
    target = str(provider_name).strip().lower()
    return [provider for provider in items if provider.name.strip().lower() == target]


def probeable_providers(providers: list[ProviderConfig], provider_name: str | None = None) -> list[ProviderConfig]:
    items = [provider for provider in providers if provider.enabled]
    if provider_name is None:
        return items
    target = str(provider_name).strip().lower()
    return [provider for provider in items if provider.name.strip().lower() == target]


def _tap_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "providers": len(records),
        "ok": sum(1 for record in records if record.get("ok")),
        "error": sum(1 for record in records if not record.get("ok")),
        "models": sum(int(record.get("model_count") or 0) for record in records),
        "drift": sum(1 for record in records if record.get("probe", {}).get("drift")),
    }
