from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

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


class ProviderRegistry(BaseModel):
    providers: list[ProviderConfig] = Field(default_factory=list)

    def enabled(self) -> list[ProviderConfig]:
        return [provider for provider in self.providers if provider.enabled]


class PolicyRegistry(BaseModel):
    profiles: dict[str, PolicyProfile] = Field(default_factory=dict)
    classification: dict[str, Any] = Field(default_factory=dict)


class AgentWorkerRegistry(BaseModel):
    agent_workers: list[AgentWorkerConfig] = Field(default_factory=list)


def load_provider_registry(path: str | Path) -> ProviderRegistry:
    return ProviderRegistry.model_validate(load_yaml(path, {"providers": []}))


def load_policy_registry(path: str | Path) -> PolicyRegistry:
    return PolicyRegistry.model_validate(load_yaml(path, {"profiles": {}, "classification": {}}))


def load_agent_worker_registry(path: str | Path) -> AgentWorkerRegistry:
    return AgentWorkerRegistry.model_validate(load_yaml(path, {"agent_workers": []}))
