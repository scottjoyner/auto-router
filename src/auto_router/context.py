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
    metadata: dict[str, Any] = Field(default_factory=dict)

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
        return sorted(objects, key=lambda item: (str(item.get("kind") or ""), str(item.get("id") or "")))

    def graph_object_summary(self) -> dict[str, int]:
        summary = {"total": 0, "node": 0, "provider": 0, "model": 0, "service": 0}
        for item in self.graph_objects():
            kind = str(item.get("kind") or "")
            summary["total"] += 1
            summary[kind] = summary.get(kind, 0) + 1
        return summary

    def provider_for(self, provider_name: str) -> ContextProvider | None:
        target = provider_name.strip().lower()
        for provider in self.providers:
            candidates = {provider.provider.lower(), *(alias.lower() for alias in provider.aliases)}
            if target in candidates:
                return provider
        return None

    def model_for(self, model_id: str) -> ContextModel | None:
        target = model_id.strip().lower()
        for model in self.all_models():
            candidates = {model.model_id.lower(), model.name.lower(), model.provider_model.lower(), *(alias.lower() for alias in model.aliases)}
            if target in candidates:
                return model
        return None

    def node_for(self, node_id: str) -> ContextNode | None:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None

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
            models[model.model_id] = model
        for provider in self.providers:
            for model in provider.models:
                models[model.model_id] = model
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
        return [model for model in self.all_models() if model.provider == provider_name]

    def services_for_node(self, node_id: str) -> list[ContextService]:
        return [service for service in self.all_services() if service.node_id == node_id]

    def services_for_provider(self, provider_name: str) -> list[ContextService]:
        return [service for service in self.all_services() if service.provider == provider_name]
