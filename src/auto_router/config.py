from __future__ import annotations

import os
import re
import time
from pathlib import Path

import httpx
from typing import Any

import yaml
from pydantic import BaseModel, Field

from auto_router.context import ContextModel, ContextNode, ContextProvider, ContextService, ContextSnapshot, ContextSignal, ExecutionLane, ServiceStatus
from auto_router.live_models import LiveModelSnapshot
from auto_router.models import AgentWorkerConfig, ModelConfig, PolicyProfile, ProviderConfig

_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2) or ""
            return os.getenv(name, default)

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def load_yaml(path: str | Path, default: Any | None = None) -> Any:
    file_path = Path(path)
    if not file_path.exists():
        return default
    with file_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or default
    return _expand_env(loaded)


def _load_json_source(source: str) -> Any:
    if not source.startswith(("http://", "https://")):
        return None
    response = httpx.get(source, timeout=10.0)
    response.raise_for_status()
    return response.json()


def _context_projection_metadata(source: str, loaded: Any, error: Exception | None = None) -> dict[str, Any]:
    is_http = str(source).startswith(("http://", "https://"))
    if error is not None:
        return {
            "projection_source": source,
            "projection_transport": "http" if is_http else "file",
            "projection_status": "bootstrap_fallback" if is_http else "bootstrap",
            "projection_error": str(error),
            "projection_reachable": False,
            "projection_loaded": bool(loaded),
        }
    return {
        "projection_source": source,
        "projection_transport": "http" if is_http else "file",
        "projection_status": "active" if is_http and loaded is not None else ("bootstrap" if not is_http else "bootstrap_fallback"),
        "projection_error": "",
        "projection_reachable": is_http and loaded is not None,
        "projection_loaded": bool(loaded),
    }


def _extract_graph_objects(loaded: Any) -> list[dict[str, Any]]:
    if isinstance(loaded, dict):
        raw = loaded.get("graph_objects") or loaded.get("objects") or loaded.get("graph")
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        return []
    if isinstance(loaded, list):
        return [item for item in loaded if isinstance(item, dict)]
    return []


def _graph_object_kind(item: dict[str, Any]) -> str:
    return str(item.get("kind") or item.get("type") or "").strip().lower()


def _graph_object_properties(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("properties")
    if isinstance(raw, dict):
        return raw
    return item


def _graph_object_identifier(item: dict[str, Any], *keys: str) -> str:
    properties = _graph_object_properties(item)
    for key in ("id", *keys):
        value = item.get(key)
        if value is None:
            value = properties.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _provider_scoped_model_id(provider: str, provider_model: str, fallback: str = "") -> str:
    provider_name = str(provider or "").strip().lower()
    model_name = str(provider_model or "").strip().lower()
    if provider_name and model_name:
        return f"{provider_name}.{model_name}"
    if provider_name and fallback:
        return f"{provider_name}.{str(fallback).strip().lower()}"
    return str(fallback or provider_model or "").strip().lower()


def _match_context_provider_key(provider: ContextProvider, candidates: dict[str, ContextProvider]) -> str | None:
    target = provider.provider.strip().lower()
    for key, candidate in candidates.items():
        if key.strip().lower() == target:
            return key
        aliases = {candidate.provider.lower(), *(alias.lower() for alias in candidate.aliases)}
        if target in aliases:
            return key
    return None


class ProviderRegistry(BaseModel):
    providers: list[ProviderConfig] = Field(default_factory=list)

    def enabled(self) -> list[ProviderConfig]:
        return [provider for provider in self.providers if provider.enabled]


class PolicyRegistry(BaseModel):
    profiles: dict[str, PolicyProfile] = Field(default_factory=dict)
    classification: dict[str, Any] = Field(default_factory=dict)


class AgentWorkerRegistry(BaseModel):
    agent_workers: list[AgentWorkerConfig] = Field(default_factory=list)


def _graph_node_context(item: dict[str, Any]) -> ContextNode | None:
    properties = _graph_object_properties(item)
    node_id = _graph_object_identifier(item, "node_id", "name")
    if not node_id:
        return None
    capabilities = properties.get("capabilities") if isinstance(properties.get("capabilities"), list) else []
    lane_value = str(properties.get("lane") or properties.get("execution_lane") or "local").strip().lower()
    lane = ExecutionLane(lane_value) if lane_value in ExecutionLane._value2member_map_ else ExecutionLane.local
    return ContextNode(
        node_id=node_id,
        display_name=str(properties.get("display_name") or properties.get("name") or node_id),
        lane=lane,
        local=bool(properties.get("local", lane == ExecutionLane.local)),
        can_use_free_api=bool(properties.get("can_use_free_api", lane == ExecutionLane.free_api)),
        running=bool(properties.get("running", True)),
        capabilities={str(item) for item in capabilities if item},
        detail=str(properties.get("detail") or properties.get("description") or ""),
    )


def _graph_provider_context(item: dict[str, Any]) -> ContextProvider | None:
    properties = _graph_object_properties(item)
    provider_name = _graph_object_identifier(item, "provider", "name")
    if not provider_name:
        return None
    capability_values = properties.get("capabilities") if isinstance(properties.get("capabilities"), list) else []
    aliases_raw = properties.get("aliases") if isinstance(properties.get("aliases"), list) else []
    model_aliases_raw = properties.get("model_aliases") if isinstance(properties.get("model_aliases"), list) else []
    aliases = _unique_list(
        [
            provider_name,
            str(properties.get("name") or ""),
            str(properties.get("type") or ""),
            str(properties.get("node_id") or ""),
            *(str(value) for value in aliases_raw if value),
            *(str(value) for value in model_aliases_raw if value),
        ]
    )
    lane_value = str(properties.get("lane") or properties.get("execution_lane") or "").strip().lower()
    local = bool(properties.get("local", lane_value == "local" or str(properties.get("quota_class") or "").strip().lower() == "local"))
    blocked = bool(properties.get("blocked", lane_value == "blocked"))
    lane = ExecutionLane.blocked if blocked else (ExecutionLane.local if local else ExecutionLane.free_api)
    return ContextProvider(
        provider=provider_name,
        lane=lane,
        local=local,
        can_use_free_api=bool(properties.get("can_use_free_api", not local and lane != ExecutionLane.blocked)),
        blocked=blocked,
        node_id=str(properties.get("node_id") or None) or None,
        aliases=aliases,
        capabilities={str(item) for item in capability_values if item},
        detail=str(properties.get("detail") or properties.get("description") or properties.get("type") or ""),
    )


def _graph_model_context(item: dict[str, Any]) -> ContextModel | None:
    properties = _graph_object_properties(item)
    model_id = _graph_object_identifier(item, "model_id", "alias", "name")
    if not model_id:
        return None
    provider = str(properties.get("provider") or properties.get("provider_name") or "").strip() or None
    provider_model = str(properties.get("provider_model") or properties.get("model") or properties.get("name") or model_id).strip()
    lane_value = str(properties.get("lane") or properties.get("execution_lane") or "").strip().lower()
    local = bool(properties.get("local", lane_value == "local" or str(properties.get("quota_class") or "").strip().lower() == "local"))
    blocked = bool(properties.get("blocked", lane_value == "blocked"))
    lane = ExecutionLane.blocked if blocked else (ExecutionLane.local if local else ExecutionLane.free_api)
    quota = properties.get("quota") if isinstance(properties.get("quota"), dict) else {}
    capability_values = properties.get("capabilities") if isinstance(properties.get("capabilities"), list) else []
    aliases_raw = properties.get("aliases") if isinstance(properties.get("aliases"), list) else []
    aliases = _unique_list(
        [
            model_id,
            provider or "",
            provider_model,
            str(properties.get("name") or ""),
            str(properties.get("alias") or ""),
            str(properties.get("type") or ""),
            *(str(value) for value in aliases_raw if value),
        ]
    )
    context_window = properties.get("context_window")
    try:
        context_window_int = int(context_window) if context_window is not None else None
    except (TypeError, ValueError):
        context_window_int = None
    priority = properties.get("priority")
    try:
        priority_int = int(priority) if priority is not None else 100
    except (TypeError, ValueError):
        priority_int = 100
    return ContextModel(
        model_id=_provider_scoped_model_id(provider or "", provider_model, model_id),
        name=str(properties.get("name") or properties.get("alias") or model_id),
        provider=provider,
        provider_model=provider_model,
        lane=lane,
        local=local,
        can_use_free_api=bool(properties.get("can_use_free_api", not local and lane != ExecutionLane.blocked)),
        blocked=blocked,
        node_id=str(properties.get("node_id") or None) or None,
        aliases=aliases,
        capabilities={str(item) for item in capability_values if item},
        context_window=context_window_int,
        quota=dict(quota),
        detail=str(properties.get("detail") or properties.get("description") or ""),
        priority=priority_int,
    )


def _graph_service_context(item: dict[str, Any]) -> ContextService | None:
    properties = _graph_object_properties(item)
    service_id = _graph_object_identifier(item, "service_id", "name")
    if not service_id:
        return None
    status_value = str(properties.get("status") or "unknown").strip().lower()
    status = ServiceStatus(status_value) if status_value in ServiceStatus._value2member_map_ else ServiceStatus.unknown
    tags_raw = properties.get("tags") if isinstance(properties.get("tags"), list) else []
    priority = properties.get("priority")
    try:
        priority_int = int(priority) if priority is not None else 100
    except (TypeError, ValueError):
        priority_int = 100
    return ContextService(
        service_id=service_id,
        name=str(properties.get("name") or service_id),
        url=str(properties.get("url") or properties.get("health_url") or ""),
        service_type=str(properties.get("service_type") or properties.get("type") or item.get("kind") or "service"),
        node_id=str(properties.get("node_id") or None) or None,
        provider=str(properties.get("provider") or None) or None,
        status=status,
        health_url=str(properties.get("health_url") or None) or None,
        tags={str(tag) for tag in tags_raw if tag},
        detail=str(properties.get("detail") or properties.get("description") or ""),
        priority=priority_int,
    )


def _graph_signal_context(item: dict[str, Any]) -> ContextSignal | None:
    properties = _graph_object_properties(item)
    target_type = str(properties.get("target_type") or properties.get("scope") or properties.get("entity_type") or "").strip().lower()
    target_id = str(
        properties.get("target_id")
        or properties.get("provider")
        or properties.get("model")
        or properties.get("node_id")
        or properties.get("service_id")
        or ""
    ).strip()
    signal_type = str(properties.get("signal_type") or properties.get("kind") or properties.get("type") or "signal").strip().lower()
    signal_id = _graph_object_identifier(item, "signal_id", "id")
    if not signal_id:
        signal_id = f"{target_type or 'target'}:{target_id or 'unknown'}:{signal_type}:{str(properties.get('source') or item.get('source') or 'assistx').strip()}"
    if not target_type or not target_id:
        return None
    strength = properties.get("strength", properties.get("weight", properties.get("score", 0.0)))
    try:
        strength_value = float(strength) if strength is not None else 0.0
    except (TypeError, ValueError):
        strength_value = 0.0
    priority = properties.get("priority")
    try:
        priority_value = int(priority) if priority is not None else 100
    except (TypeError, ValueError):
        priority_value = 100
    observed_at = properties.get("observed_at", properties.get("created_at", properties.get("fetched_at", int(time.time()))))
    try:
        observed_at_value = int(observed_at)
    except (TypeError, ValueError):
        observed_at_value = int(time.time())
    expires_at = properties.get("expires_at")
    try:
        expires_at_value = int(expires_at) if expires_at is not None else None
    except (TypeError, ValueError):
        expires_at_value = None
    tags_value = properties.get("tags") or []
    tags_raw = tags_value if isinstance(tags_value, list) else []
    metadata = dict(properties)
    return ContextSignal(
        signal_id=signal_id,
        target_type=target_type,
        target_id=target_id,
        signal_type=signal_type,
        source=str(properties.get("source") or item.get("source") or "assistx"),
        strength=strength_value,
        active=bool(properties.get("active", True)),
        observed_at=observed_at_value,
        expires_at=expires_at_value,
        priority=priority_value,
        detail=str(properties.get("detail") or properties.get("description") or ""),
        tags={str(tag) for tag in tags_raw if tag},
        metadata=metadata,
    )


def _project_graph_objects(snapshot: ContextSnapshot, loaded: Any) -> ContextSnapshot:
    objects = _extract_graph_objects(loaded)
    if not objects:
        return snapshot

    graph_nodes: list[ContextNode] = []
    graph_providers: list[ContextProvider] = []
    graph_models: list[ContextModel] = []
    graph_services: list[ContextService] = []
    graph_signals: list[ContextSignal] = []

    for item in objects:
        kind = _graph_object_kind(item)
        if kind in {"node", "machine", "host"}:
            node = _graph_node_context(item)
            if node is not None:
                graph_nodes.append(node)
        elif kind == "provider":
            provider = _graph_provider_context(item)
            if provider is not None:
                graph_providers.append(provider)
        elif kind == "model":
            model = _graph_model_context(item)
            if model is not None:
                graph_models.append(model)
        elif kind == "service":
            service = _graph_service_context(item)
            if service is not None:
                graph_services.append(service)
        elif kind == "signal":
            signal = _graph_signal_context(item)
            if signal is not None:
                graph_signals.append(signal)

    snapshot.nodes = _merge_nodes(list(snapshot.nodes), graph_nodes)
    snapshot.providers = _merge_context_providers({provider.provider: provider for provider in snapshot.providers}, graph_providers)
    snapshot.models = _merge_models({model.model_id: model for model in snapshot.models}, graph_models)
    snapshot.services = _merge_services(list(snapshot.services), graph_services)
    snapshot.signals = _merge_signals(list(snapshot.signals), graph_signals)

    if snapshot.providers:
        provider_models: dict[str, list[ContextModel]] = {}
        provider_services: dict[str, list[ContextService]] = {}
        for model in snapshot.models:
            if model.provider:
                provider_models.setdefault(model.provider, []).append(model)
        for service in snapshot.services:
            if service.provider:
                provider_services.setdefault(service.provider, []).append(service)
        for provider in snapshot.providers:
            provider.models = _merge_models(
                {model.model_id: model for model in provider.models},
                provider_models.get(provider.provider, []),
            )
            provider.services = _merge_services(provider.services, provider_services.get(provider.provider, []))

    if snapshot.nodes and snapshot.services:
        node_services: dict[str, list[ContextService]] = {}
        for service in snapshot.services:
            if service.node_id:
                node_services.setdefault(service.node_id, []).append(service)
        for node in snapshot.nodes:
            node.services = _merge_services(node.services, node_services.get(node.node_id, []))

    return snapshot


def _lane_from_provider(provider: ProviderConfig) -> ExecutionLane:
    quota_class = str(provider.quota_class)
    if quota_class == "local" or provider.type == "lmstudio":
        return ExecutionLane.local
    if quota_class == "blocked":
        return ExecutionLane.blocked
    return ExecutionLane.free_api


def _model_context(provider: ProviderConfig, model: ModelConfig) -> ContextModel:
    lane = _lane_from_provider(provider)
    provider_key = provider.id or provider.name
    aliases = [model.alias, provider_key, provider.name, provider.type, model.provider_model]
    context_detail = f"{provider.type} model {model.provider_model}"
    if model.context_window:
        context_detail += f" · ctx {model.context_window}"
    if model.capabilities:
        context_detail += f" · {len(model.capabilities)} capability(s)"
    return ContextModel(
        model_id=_provider_scoped_model_id(provider_key, model.provider_model, model.alias),
        name=model.alias,
        provider=provider_key,
        provider_model=model.provider_model,
        lane=lane,
        local=lane == ExecutionLane.local,
        can_use_free_api=lane == ExecutionLane.free_api,
        blocked=lane == ExecutionLane.blocked,
        node_id=provider.node_id,
        aliases=_unique_list([alias for alias in aliases if alias]),
        capabilities=set(model.capabilities),
        context_window=model.context_window,
        quota=dict(model.quota),
        detail=context_detail,
        priority=provider.priority,
    )


def _merge_live_model(
    base: ContextModel,
    overlay: ContextModel,
) -> ContextModel:
    aliases = _unique_list([*overlay.aliases, *base.aliases])
    detail = overlay.detail or base.detail
    if base.detail and overlay.detail:
        detail = f"{base.detail} · {overlay.detail}"
    return base.model_copy(
        update={
            "name": base.name or overlay.name,
            "provider": base.provider or overlay.provider,
            "provider_model": base.provider_model or overlay.provider_model,
            "lane": base.lane if base.lane is not None else overlay.lane,
            "local": base.local or overlay.local,
            "can_use_free_api": base.can_use_free_api or overlay.can_use_free_api,
            "blocked": base.blocked or overlay.blocked,
            "node_id": base.node_id or overlay.node_id,
            "aliases": aliases,
            "capabilities": set(base.capabilities) | set(overlay.capabilities),
            "context_window": base.context_window if base.context_window is not None else overlay.context_window,
            "quota": dict(base.quota) or dict(overlay.quota),
            "detail": detail,
            "services": _merge_services(list(base.services), list(overlay.services)),
            "priority": min(base.priority, overlay.priority),
        }
    )


def _live_model_context(
    provider: ProviderConfig,
    model_record: dict[str, Any],
    context_provider: ContextProvider | None = None,
) -> ContextModel:
    provider_key = provider.id or provider.name
    raw_id = str(model_record.get("id") or model_record.get("name") or model_record.get("model") or "").strip()
    safe_id = raw_id or f"live-{provider_key}"
    lane = _lane_from_provider(provider)
    aliases = [safe_id, provider_key, provider.name, provider.type, str(model_record.get("owned_by") or "")]
    detail = f"live /models snapshot from {provider.type}"
    owned_by = str(model_record.get("owned_by") or "").strip()
    if owned_by:
        detail += f" · owned by {owned_by}"
    if context_provider and context_provider.detail:
        detail += f" · {context_provider.detail}"
    return ContextModel(
        model_id=_provider_scoped_model_id(provider_key, safe_id, safe_id),
        name=safe_id,
        provider=provider_key,
        provider_model=safe_id,
        lane=lane,
        local=lane == ExecutionLane.local,
        can_use_free_api=lane == ExecutionLane.free_api,
        blocked=lane == ExecutionLane.blocked,
        node_id=provider.node_id,
        aliases=_unique_list(aliases),
        capabilities=set(),
        detail=detail,
        priority=provider.priority,
    )


def _project_live_models(
    snapshot: ContextSnapshot,
    providers: ProviderRegistry,
    live_snapshots: list[LiveModelSnapshot],
) -> ContextSnapshot:
    provider_map = {provider.id or provider.name: provider for provider in providers.providers}
    live_model_map = {model.model_id: model for model in snapshot.models}
    for live_snapshot in live_snapshots:
        provider_config = provider_map.get(live_snapshot.provider)
        if provider_config is None:
            continue
        provider_context = snapshot.provider_for(live_snapshot.provider)
        for record in live_snapshot.models:
            live_model = _live_model_context(provider_config, record, provider_context)
            matched = snapshot.model_for(live_model.provider_model) or snapshot.model_for(live_model.name)
            if matched is None:
                live_model_map[live_model.model_id] = live_model
                continue
            live_model_map[matched.model_id] = _merge_live_model(matched, live_model)
    snapshot.models = sorted(live_model_map.values(), key=lambda item: (item.priority, item.provider or "", item.name.lower()))
    for provider in snapshot.providers:
        provider.models = [model for model in snapshot.models if model.provider == provider.provider]
    return snapshot


def _provider_context(provider: ProviderConfig) -> ContextProvider:
    lane = _lane_from_provider(provider)
    aliases = [provider.id or provider.name, provider.name, provider.type]
    model_aliases = [model.alias for model in provider.models if model.alias]
    aliases.extend(sorted(set(model_aliases)))
    model_detail = ""
    if model_aliases:
        visible = ", ".join(model_aliases[:3])
        remaining = len(model_aliases) - min(len(model_aliases), 3)
        model_detail = f" · models: {visible}" + (f" +{remaining} more" if remaining > 0 else "")
    return ContextProvider(
        provider=provider.name,
        lane=lane,
        local=lane == ExecutionLane.local,
        can_use_free_api=lane == ExecutionLane.free_api,
        blocked=lane == ExecutionLane.blocked,
        node_id=provider.node_id,
        aliases=[alias for alias in aliases if alias],
        capabilities={cap for model in provider.models for cap in model.capabilities},
        detail=f"{provider.type}{model_detail}",
        models=[_model_context(provider, model) for model in provider.models],
    )


def _provider_service(provider: ProviderConfig) -> ContextService:
    base_url = provider.base_url.rstrip("/")
    health_url = f"{base_url}/models" if not base_url.endswith("/models") else base_url
    model_aliases = [model.alias for model in provider.models if model.alias]
    if model_aliases:
        visible = ", ".join(model_aliases[:3])
        remaining = len(model_aliases) - min(len(model_aliases), 3)
        detail = f"{provider.type} endpoint for {len(model_aliases)} model(s): {visible}"
        if remaining > 0:
            detail += f" +{remaining} more"
    else:
        detail = f"{provider.type} endpoint"
    tags = {provider.type, "provider", "model_endpoint"}
    if provider.gateway_managed:
        tags.add("gateway_managed")
    if provider.local_gateway_only:
        tags.add("local_gateway_only")
    return ContextService(
        service_id=f"provider.{provider.id or provider.name}.api",
        name=f"{provider.name} API",
        url=provider.base_url,
        service_type="lmstudio" if provider.type == "lmstudio" else "openai_compatible_api",
        node_id=provider.node_id,
        provider=provider.id or provider.name,
        health_url=health_url,
        status=ServiceStatus.unknown,
        tags=tags,
        detail=detail,
        priority=provider.priority,
    )


def _unique_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _merge_services(*groups: list[ContextService]) -> list[ContextService]:
    merged: dict[str, ContextService] = {}
    for group in groups:
        for service in group:
            merged[service.service_id] = service
    return list(merged.values())


def _merge_nodes(*groups: list[ContextNode]) -> list[ContextNode]:
    merged: dict[str, ContextNode] = {}
    for group in groups:
        for node in group:
            existing = merged.get(node.node_id)
            if existing is None:
                merged[node.node_id] = node
                continue
            node.services = _merge_services(list(node.services), list(existing.services))
            node.capabilities = set(node.capabilities) | set(existing.capabilities)
            if not node.display_name:
                node.display_name = existing.display_name
            if not node.detail:
                node.detail = existing.detail
            node.running = node.running or existing.running
            node.local = node.local or existing.local
            node.can_use_free_api = node.can_use_free_api or existing.can_use_free_api
            merged[node.node_id] = node
    return list(merged.values())


def _merge_signals(*groups: list[ContextSignal]) -> list[ContextSignal]:
    merged: dict[str, ContextSignal] = {}
    for group in groups:
        for signal in group:
            existing = merged.get(signal.signal_id)
            if existing is None or signal.observed_at >= existing.observed_at:
                merged[signal.signal_id] = signal
    return sorted(merged.values(), key=lambda item: (item.priority, item.source, item.target_type, item.target_id, item.signal_type))


def _merge_models(
    bootstrap_models: dict[str, ContextModel],
    snapshot_models: list[ContextModel],
) -> list[ContextModel]:
    merged: dict[str, ContextModel] = dict(bootstrap_models)
    for model in snapshot_models:
        bootstrap_model = merged.get(model.model_id)
        if bootstrap_model is None:
            merged[model.model_id] = model
            continue
        model.aliases = _unique_list([*model.aliases, *bootstrap_model.aliases])
        model.capabilities = set(model.capabilities) | set(bootstrap_model.capabilities)
        model.services = _merge_services(list(model.services), list(bootstrap_model.services))
        if not model.detail:
            model.detail = bootstrap_model.detail
        if not model.provider:
            model.provider = bootstrap_model.provider
        if not model.provider_model:
            model.provider_model = bootstrap_model.provider_model
        if model.node_id is None:
            model.node_id = bootstrap_model.node_id
        if model.context_window is None:
            model.context_window = bootstrap_model.context_window
        if not model.quota:
            model.quota = dict(bootstrap_model.quota)
        if model.priority == 100:
            model.priority = bootstrap_model.priority
        merged[model.model_id] = model
    return list(merged.values())


def _merge_context_providers(
    bootstrap_providers: dict[str, ContextProvider],
    snapshot_providers: list[ContextProvider],
) -> list[ContextProvider]:
    merged: dict[str, ContextProvider] = dict(bootstrap_providers)
    for provider in snapshot_providers:
        provider_key = _match_context_provider_key(provider, merged)
        bootstrap_provider = merged.get(provider_key) if provider_key is not None else None
        if bootstrap_provider is None:
            merged[provider.provider] = provider
            continue
        canonical_provider = bootstrap_provider.provider
        if provider.provider != canonical_provider:
            provider = provider.model_copy(
                update={
                    "provider": canonical_provider,
                    "models": [model.model_copy(update={"provider": canonical_provider}) for model in provider.models],
                    "services": [service.model_copy(update={"provider": canonical_provider}) for service in provider.services],
                }
            )
        provider.aliases = _unique_list([*provider.aliases, *bootstrap_provider.aliases])
        provider.capabilities = set(provider.capabilities) | set(bootstrap_provider.capabilities)
        provider.services = _merge_services(list(provider.services), list(bootstrap_provider.services))
        provider.models = _merge_models({model.model_id: model for model in bootstrap_provider.models}, list(provider.models))
        if not provider.detail:
            provider.detail = bootstrap_provider.detail
        if provider.node_id is None:
            provider.node_id = bootstrap_provider.node_id
        if provider.free_api_credits is None:
            provider.free_api_credits = bootstrap_provider.free_api_credits
        merged[canonical_provider] = provider
    return list(merged.values())


def _bootstrap_context(snapshot: ContextSnapshot, providers: ProviderRegistry, agents: AgentWorkerRegistry) -> ContextSnapshot:
    bootstrap_provider_map = {provider.id or provider.name: _provider_context(provider) for provider in providers.providers}
    bootstrap_services = [_provider_service(provider) for provider in providers.providers]
    bootstrap_models = {
        model.model_id: model
        for provider in bootstrap_provider_map.values()
        for model in provider.models
    }
    snapshot.providers = _merge_context_providers(bootstrap_provider_map, list(snapshot.providers))
    snapshot.models = _merge_models(bootstrap_models, list(snapshot.models))
    for provider in snapshot.providers:
        provider.services = _merge_services(
            [service for service in bootstrap_services if service.provider == provider.provider],
            list(provider.services),
        )
        bootstrap_provider = bootstrap_provider_map.get(provider.provider)
        provider.models = _merge_models(
            {model.model_id: model for model in (bootstrap_provider.models if bootstrap_provider else [])},
            list(provider.models),
        )
    snapshot.services = _merge_services(bootstrap_services, list(snapshot.services))
    bootstrap_nodes = {node.node_id: node for node in (_node_context(item) for item in agents.agent_workers)}
    for node in snapshot.nodes:
        bootstrap_nodes[node.node_id] = node
    snapshot.nodes = list(bootstrap_nodes.values())
    return snapshot


def _node_context(worker: AgentWorkerConfig) -> ContextNode:
    capabilities = set()
    if isinstance(worker.policy, dict):
        raw_caps = worker.policy.get("capabilities")
        if isinstance(raw_caps, list):
            capabilities.update(str(item) for item in raw_caps if item)
    capabilities.update(str(item) for item in worker.toolsets if str(item).strip())
    if worker.launcher:
        capabilities.add(f"launcher:{worker.launcher}")
    return ContextNode(
        node_id=worker.name,
        display_name=worker.name,
        lane=ExecutionLane.local if worker.enabled else ExecutionLane.blocked,
        local=True,
        can_use_free_api=False,
        running=worker.enabled,
        capabilities=capabilities,
        detail=worker.type,
    )


def load_provider_registry(path: str | Path) -> ProviderRegistry:
    return ProviderRegistry.model_validate(load_yaml(path, {"providers": []}))


def load_policy_registry(path: str | Path) -> PolicyRegistry:
    return PolicyRegistry.model_validate(load_yaml(path, {"profiles": {}, "classification": {}}))


def load_agent_worker_registry(path: str | Path) -> AgentWorkerRegistry:
    return AgentWorkerRegistry.model_validate(load_yaml(path, {"agent_workers": []}))


def load_context_snapshot(
    path: str | Path,
    providers: ProviderRegistry,
    agents: AgentWorkerRegistry,
) -> ContextSnapshot:
    source = str(path)
    loaded = None
    projection_error: Exception | None = None
    if source.startswith(("http://", "https://")):
        try:
            loaded = _load_json_source(source)
        except Exception as exc:
            projection_error = exc
            loaded = None
    else:
        loaded = load_yaml(path, None)

    if loaded:
        snapshot = ContextSnapshot.model_validate(loaded)
    else:
        snapshot = ContextSnapshot(revision="bootstrap", source=source)

    snapshot = _project_graph_objects(snapshot, loaded)
    snapshot = _bootstrap_context(snapshot, providers, agents)
    snapshot.metadata = {**dict(snapshot.metadata or {}), **_context_projection_metadata(source, loaded, projection_error)}

    if not snapshot.source:
        snapshot.source = source
    return snapshot


async def _load_json_source_async(source: str) -> Any:
    if not source.startswith(("http://", "https://")):
        return None
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(source)
        response.raise_for_status()
        return response.json()


async def load_context_snapshot_async(
    path: str | Path,
    providers: ProviderRegistry,
    agents: AgentWorkerRegistry,
) -> ContextSnapshot:
    source = str(path)
    loaded = None
    projection_error: Exception | None = None
    if source.startswith(("http://", "https://")):
        try:
            loaded = await _load_json_source_async(source)
        except Exception as exc:
            projection_error = exc
            loaded = None
    else:
        loaded = load_yaml(path, None)

    if loaded:
        snapshot = ContextSnapshot.model_validate(loaded)
    else:
        snapshot = ContextSnapshot(revision="bootstrap", source=source)

    snapshot = _project_graph_objects(snapshot, loaded)
    snapshot = _bootstrap_context(snapshot, providers, agents)
    snapshot.metadata = {**dict(snapshot.metadata or {}), **_context_projection_metadata(source, loaded, projection_error)}

    if not snapshot.source:
        snapshot.source = source
    return snapshot
