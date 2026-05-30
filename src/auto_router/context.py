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
    services: list[ContextService] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def provider_for(self, provider_name: str) -> ContextProvider | None:
        target = provider_name.strip().lower()
        for provider in self.providers:
            candidates = {provider.provider.lower(), *(alias.lower() for alias in provider.aliases)}
            if target in candidates:
                return provider
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

    def services_for_node(self, node_id: str) -> list[ContextService]:
        return [service for service in self.all_services() if service.node_id == node_id]

    def services_for_provider(self, provider_name: str) -> list[ContextService]:
        return [service for service in self.all_services() if service.provider == provider_name]
