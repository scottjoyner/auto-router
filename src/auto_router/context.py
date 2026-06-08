from __future__ import annotations

import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ExecutionLane(StrEnum):
    local = "local"
    free_api = "free_api"
    paperclip = "paperclip"
    blocked = "blocked"


class ServiceStatus(StrEnum):
    unknown = "unknown"
    online = "online"
    degraded = "degraded"
    offline = "offline"
    blocked = "blocked"


class ContextService(BaseModel):
    service_id: str
    name: str
    url: str
    service_type: str = "service"
    node_id: str | None = None
    provider: str | None = None
    status: ServiceStatus = ServiceStatus.unknown
    health_url: str | None = None
    tags: set[str] = Field(default_factory=set)
    detail: str = ""
    priority: int = 100

    @property
    def is_online(self) -> bool:
        return self.status == ServiceStatus.online


class ContextSignal(BaseModel):
    signal_id: str
    target_type: str
    target_id: str
    signal_type: str
    source: str = ""
    strength: float = 0.0
    active: bool = True
    observed_at: int = Field(default_factory=lambda: int(time.time()))
    expires_at: int | None = None
    priority: int = 100
    detail: str = ""
    tags: set[str] = Field(default_factory=set)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and int(time.time()) >= self.expires_at

    @property
    def is_active(self) -> bool:
        return self.active and not self.is_expired

    @property
    def is_blocking(self) -> bool:
        return self.is_active and self.signal_type in {"blocked", "disabled", "disallowed"}


class ContextNode(BaseModel):
    node_id: str
    display_name: str | None = None
    lane: ExecutionLane = ExecutionLane.local
    local: bool = True
    can_use_free_api: bool = False
    running: bool = True
    capabilities: set[str] = Field(default_factory=set)
    detail: str = ""
    services: list[ContextService] = Field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return self.lane == ExecutionLane.blocked


class ContextModel(BaseModel):
    model_id: str
    name: str
    provider: str | None = None
    provider_model: str = ""
    lane: ExecutionLane = ExecutionLane.free_api
    local: bool = False
    can_use_free_api: bool = True
    blocked: bool = False
    node_id: str | None = None
    aliases: list[str] = Field(default_factory=list)
    capabilities: set[str] = Field(default_factory=set)
    context_window: int | None = None
    quota: dict[str, int] = Field(default_factory=dict)
    detail: str = ""
    services: list[ContextService] = Field(default_factory=list)
    priority: int = 100

    @property
    def is_blocked(self) -> bool:
        return self.blocked or self.lane == ExecutionLane.blocked

    @property
    def is_local(self) -> bool:
        return self.local or self.lane == ExecutionLane.local

    @property
    def is_free_api(self) -> bool:
        return self.can_use_free_api and self.lane == ExecutionLane.free_api


class ContextProvider(BaseModel):
    provider: str
    lane: ExecutionLane = ExecutionLane.free_api
    local: bool = False
    can_use_free_api: bool = True
    free_api_credits: int | None = None
    blocked: bool = False
    node_id: str | None = None
    aliases: list[str] = Field(default_factory=list)
    capabilities: set[str] = Field(default_factory=set)
    detail: str = ""
    services: list[ContextService] = Field(default_factory=list)
    models: list[ContextModel] = Field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return self.blocked or self.lane == ExecutionLane.blocked

    @property
    def is_local(self) -> bool:
        return self.local or self.lane == ExecutionLane.local

    @property
    def is_free_api(self) -> bool:
        return self.can_use_free_api and self.lane == ExecutionLane.free_api


class ContextSnapshot(BaseModel):
    revision: str = "bootstrap"
    source: str = ""
    generated_at: int = Field(default_factory=lambda: int(time.time()))
    nodes: list[ContextNode] = Field(default_factory=list)
    providers: list[ContextProvider] = Field(default_factory=list)
    models: list[ContextModel] = Field(default_factory=list)
    services: list[ContextService] = Field(default_factory=list)
    signals: list[ContextSignal] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def projection_status(self) -> str:
        status = str(self.metadata.get("projection_status") or self.metadata.get("projection_state") or "").strip().lower()
        if status:
            return status
        if str(self.source or "").startswith(("http://", "https://")):
            return "bootstrap_fallback" if self.revision == "bootstrap" else "active"
        return "bootstrap" if self.revision == "bootstrap" else "active"

    def projection_error(self) -> str:
        return str(self.metadata.get("projection_error") or "")

    def is_projection_degraded(self) -> bool:
        return self.projection_status() in {"bootstrap_fallback", "degraded", "error", "unavailable"}

    def canonical_provider_name(self, provider_name: str) -> str:
        provider = self.provider_for(provider_name)
        if provider is not None:
            return str(provider.provider).strip().lower()
        return str(provider_name).strip().lower()

    def canonical_node_id(self, node_id: str) -> str:
        target = node_id.strip().lower()
        for node in self.nodes:
            if node.node_id.strip().lower() == target:
                return node.node_id
        return node_id.strip()

    def canonical_model_id(self, model_id: str) -> str:
        model = self.model_for(model_id)
        if model is not None:
            return model.model_id
        return str(model_id).strip().lower()

    def canonical_signal_target(self, target_type: str, target_id: str) -> str:
        target_type_normalized = target_type.strip().lower()
        if target_type_normalized == "provider":
            return self.canonical_provider_name(target_id)
        if target_type_normalized == "model":
            return self.canonical_model_id(target_id)
        if target_type_normalized == "node":
            return self.canonical_node_id(target_id)
        return str(target_id).strip().lower()

    def graph_objects(self) -> list[dict[str, Any]]:
        source = str(self.source or self.revision or "bootstrap")
        objects: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def add(kind: str, object_id: str, properties: dict[str, Any]) -> None:
            key = (kind, object_id)
            if key in seen:
                return
            seen.add(key)
            objects.append({"kind": kind, "id": object_id, "source": source, "properties": properties})

        for node in self.nodes:
            add("node", node.node_id, node.model_dump(mode="json"))
        for provider in self.providers:
            add("provider", provider.provider, provider.model_dump(mode="json"))
            for model in provider.models:
                add("model", model.model_id, model.model_dump(mode="json"))
            for service in provider.services:
                add("service", service.service_id, service.model_dump(mode="json"))
        for model in self.models:
            add("model", model.model_id, model.model_dump(mode="json"))
        for service in self.services:
            add("service", service.service_id, service.model_dump(mode="json"))
        for signal in self.signals:
            add("signal", signal.signal_id, signal.model_dump(mode="json"))
        return sorted(objects, key=lambda item: (str(item.get("kind") or ""), str(item.get("id") or "")))

    def graph_object_summary(self) -> dict[str, int]:
        summary = {"total": 0, "node": 0, "provider": 0, "model": 0, "service": 0, "signal": 0}
        for item in self.graph_objects():
            kind = str(item.get("kind") or "")
            summary["total"] += 1
            summary[kind] = summary.get(kind, 0) + 1
        return summary

    def provider_for(self, provider_name: str) -> ContextProvider | None:
        target = provider_name.strip().lower()
        for provider in self.providers:
            candidates = {provider.provider.lower(), *(alias.lower() for alias in provider.aliases)}
            provider_id = getattr(provider, "id", "")
            if provider_id:
                candidates.add(str(provider_id).strip().lower())
            if target in candidates:
                return provider
        return None

    def model_for(self, model_id: str) -> ContextModel | None:
        target = model_id.strip().lower()
        for model in self.all_models():
            provider_prefix = str(model.provider or "").strip().lower()
            scoped_candidates = set()
            if provider_prefix:
                scoped_candidates.add(f"{provider_prefix}.{model.name.lower()}")
                scoped_candidates.add(f"{provider_prefix}.{model.model_id.lower()}")
                scoped_candidates.update({f"{provider_prefix}.{alias.lower()}" for alias in model.aliases})
            candidates = {
                self._model_key(model),
                model.model_id.lower(),
                model.name.lower(),
                model.provider_model.lower(),
                *(alias.lower() for alias in model.aliases),
                *scoped_candidates,
            }
            if target in candidates:
                return model
        return None

    def node_for(self, node_id: str) -> ContextNode | None:
        target = node_id.strip().lower()
        for node in self.nodes:
            if node.node_id.strip().lower() == target:
                return node
        return None

    def signals_for(self, target_type: str, target_id: str) -> list[ContextSignal]:
        target_type_normalized = target_type.strip().lower()
        target_id_normalized = self.canonical_signal_target(target_type_normalized, target_id)
        return [
            signal
            for signal in self.signals
            if signal.is_active
            and signal.target_type.strip().lower() == target_type_normalized
            and self.canonical_signal_target(signal.target_type, signal.target_id) == target_id_normalized
        ]

    def all_signals(self) -> list[ContextSignal]:
        return sorted(
            [signal for signal in self.signals if signal.is_active],
            key=lambda item: (item.priority, item.source, item.target_type, item.target_id, item.signal_type),
        )

    def signals_for_provider(self, provider_name: str) -> list[ContextSignal]:
        return self.signals_for("provider", provider_name)

    def signals_for_model(self, model_id: str) -> list[ContextSignal]:
        return self.signals_for("model", model_id)

    def signals_for_node(self, node_id: str) -> list[ContextSignal]:
        return self.signals_for("node", node_id)

    def signal_summary(self) -> dict[str, int]:
        summary = {"total": 0, "active": 0, "provider": 0, "model": 0, "node": 0, "service": 0}
        for signal in self.signals:
            summary["total"] += 1
            if signal.is_active:
                summary["active"] += 1
            target_type = signal.target_type.strip().lower()
            summary[target_type] = summary.get(target_type, 0) + 1
        return summary

    def local_provider_names(self) -> list[str]:
        return [provider.provider for provider in self.providers if provider.is_local]

    def free_api_provider_names(self) -> list[str]:
        return [provider.provider for provider in self.providers if provider.is_free_api]

    def blocked_provider_names(self) -> list[str]:
        return [provider.provider for provider in self.providers if provider.is_blocked]

    def running_local_node_names(self) -> list[str]:
        return [node.node_id for node in self.nodes if node.local and node.running and not node.is_blocked]

    def all_models(self) -> list[ContextModel]:
        models: dict[str, ContextModel] = {}
        for model in self.models:
            models[self._model_key(model)] = model
        for provider in self.providers:
            for model in provider.models:
                models[self._model_key(model)] = model
        return sorted(models.values(), key=lambda item: (item.priority, item.provider or "", item.name.lower()))

    def local_models(self) -> list[ContextModel]:
        return [model for model in self.all_models() if model.is_local and not model.is_blocked]

    def free_api_models(self) -> list[ContextModel]:
        return [model for model in self.all_models() if model.is_free_api]

    def blocked_models(self) -> list[ContextModel]:
        return [model for model in self.all_models() if model.is_blocked]

    def models_by_lane(self, lane: ExecutionLane) -> list[ContextModel]:
        if lane == ExecutionLane.local:
            return self.local_models()
        if lane == ExecutionLane.free_api:
            return self.free_api_models()
        if lane == ExecutionLane.blocked:
            return self.blocked_models()
        return [model for model in self.all_models() if model.lane == lane]

    def all_services(self) -> list[ContextService]:
        services: dict[str, ContextService] = {}
        for service in self.services:
            services[service.service_id] = service
        for node in self.nodes:
            for service in node.services:
                services[service.service_id] = service
        for provider in self.providers:
            for service in provider.services:
                services[service.service_id] = service
        return sorted(services.values(), key=lambda item: (item.priority, item.name.lower()))

    def models_for_provider(self, provider_name: str) -> list[ContextModel]:
        provider = self.provider_for(provider_name)
        target = provider.provider if provider is not None else provider_name.strip().lower()
        return [model for model in self.all_models() if (model.provider or "").strip().lower() == target]

    def services_for_node(self, node_id: str) -> list[ContextService]:
        return [service for service in self.all_services() if service.node_id == node_id]

    def services_for_provider(self, provider_name: str) -> list[ContextService]:
        target = self.canonical_provider_name(provider_name)
        return [service for service in self.all_services() if (service.provider or "").strip().lower() == target]

    def _model_key(self, model: ContextModel) -> str:
        provider = str(model.provider or "").strip().lower()
        provider_model = str(model.provider_model or "").strip().lower()
        if provider and provider_model:
            return f"{provider}.{provider_model}"
        if model.model_id:
            return model.model_id.strip().lower()
        if model.name:
            return model.name.strip().lower()
        return ""
