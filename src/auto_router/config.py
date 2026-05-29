from __future__ import annotations

import os
import re
from pathlib import Path

import httpx
from typing import Any

import yaml
from pydantic import BaseModel, Field

from auto_router.context import ContextNode, ContextProvider, ContextSnapshot, ExecutionLane
from auto_router.models import AgentWorkerConfig, PolicyProfile, ProviderConfig

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


class ProviderRegistry(BaseModel):
    providers: list[ProviderConfig] = Field(default_factory=list)

    def enabled(self) -> list[ProviderConfig]:
        return [provider for provider in self.providers if provider.enabled]


class PolicyRegistry(BaseModel):
    profiles: dict[str, PolicyProfile] = Field(default_factory=dict)
    classification: dict[str, Any] = Field(default_factory=dict)


class AgentWorkerRegistry(BaseModel):
    agent_workers: list[AgentWorkerConfig] = Field(default_factory=list)


def _lane_from_provider(provider: ProviderConfig) -> ExecutionLane:
    quota_class = str(provider.quota_class)
    if quota_class == "local" or provider.type == "lmstudio":
        return ExecutionLane.local
    if quota_class == "blocked":
        return ExecutionLane.blocked
    return ExecutionLane.free_api


def _provider_context(provider: ProviderConfig) -> ContextProvider:
    lane = _lane_from_provider(provider)
    aliases = [provider.name, provider.type]
    aliases.extend(sorted({model.alias for model in provider.models if model.alias}))
    return ContextProvider(
        provider=provider.name,
        lane=lane,
        local=lane == ExecutionLane.local,
        can_use_free_api=lane == ExecutionLane.free_api,
        blocked=lane == ExecutionLane.blocked,
        node_id=provider.node_id,
        aliases=[alias for alias in aliases if alias],
        capabilities={cap for model in provider.models for cap in model.capabilities},
        detail=provider.type,
    )


def _node_context(worker: AgentWorkerConfig) -> ContextNode:
    capabilities = set()
    if isinstance(worker.policy, dict):
        raw_caps = worker.policy.get("capabilities")
        if isinstance(raw_caps, list):
            capabilities.update(str(item) for item in raw_caps if item)
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


def _load_json_source(source: str) -> Any:
    if not source.startswith(("http://", "https://")):
        return None
    response = httpx.get(source, timeout=10.0)
    response.raise_for_status()
    return response.json()


def load_context_snapshot(
    path: str | Path,
    providers: ProviderRegistry,
    agents: AgentWorkerRegistry,
) -> ContextSnapshot:
    source = str(path)
    loaded = None
    if source.startswith(("http://", "https://")):
        try:
            loaded = _load_json_source(source)
        except Exception:
            loaded = None
    else:
        loaded = load_yaml(path, None)
    
    if loaded:
        snapshot = ContextSnapshot.model_validate(loaded)
    else:
        snapshot = ContextSnapshot(revision="bootstrap", source=source)

    bootstrap_providers = {provider.provider: provider for provider in (_provider_context(item) for item in providers.providers)}
    for provider in snapshot.providers:
        bootstrap_providers[provider.provider] = provider
        for alias in getattr(provider, "aliases", []) or []:
            bootstrap_providers.setdefault(alias, provider)
    snapshot.providers = list(bootstrap_providers.values())

    bootstrap_nodes = {node.node_id: node for node in (_node_context(item) for item in agents.agent_workers)}
    for node in snapshot.nodes:
        bootstrap_nodes[node.node_id] = node
    snapshot.nodes = list(bootstrap_nodes.values())

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
    if source.startswith(("http://", "https://")):
        try:
            loaded = await _load_json_source_async(source)
        except Exception:
            loaded = None
    else:
        loaded = load_yaml(path, None)
    
    if loaded:
        snapshot = ContextSnapshot.model_validate(loaded)
    else:
        snapshot = ContextSnapshot(revision="bootstrap", source=source)

    bootstrap_providers = {provider.provider: provider for provider in (_provider_context(item) for item in providers.providers)}
    for provider in snapshot.providers:
        bootstrap_providers[provider.provider] = provider
        for alias in getattr(provider, "aliases", []) or []:
            bootstrap_providers.setdefault(alias, provider)
    snapshot.providers = list(bootstrap_providers.values())

    bootstrap_nodes = {node.node_id: node for node in (_node_context(item) for item in agents.agent_workers)}
    for node in snapshot.nodes:
        bootstrap_nodes[node.node_id] = node
    snapshot.nodes = list(bootstrap_nodes.values())

    if not snapshot.source:
        snapshot.source = source
    return snapshot
