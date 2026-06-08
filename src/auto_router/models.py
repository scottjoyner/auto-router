from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Priority(StrEnum):
    critical = "critical"
    repo_critical = "repo_critical"
    interactive = "interactive"
    batch = "batch"
    background = "background"
    local_only = "local_only"


class StagePurpose(StrEnum):
    draft = "draft"
    refine = "refine"
    judge = "judge"
    repair = "repair"
    final = "final"


class QuotaClass(StrEnum):
    premium_free = "premium_free"
    fast_free = "fast_free"
    edge_free = "edge_free"
    brokered_free = "brokered_free"
    local = "local"


class ModelConfig(BaseModel):
    alias: str
    provider_model: str
    capabilities: set[str] = Field(default_factory=set)
    context_window: int | None = None
    quota: dict[str, int] = Field(default_factory=dict)


class ProviderConfig(BaseModel):
    id: str = Field(default="")
    name: str
    type: str
    node_id: str | None = None
    enabled: bool = True
    base_url: str
    api_key_env: str | None = None
    priority: int = 100
    quota_class: QuotaClass | str = QuotaClass.local
    headers: dict[str, str] = Field(default_factory=dict)
    models: list[ModelConfig] = Field(default_factory=list)
    gateway_managed: bool = False  # Route through agentgateway when enabled
    local_gateway_only: bool = False  # Only use gateway for local-only requests


class PolicyStage(BaseModel):
    purpose: StagePurpose
    provider_classes: list[str] = Field(default_factory=list)
    required_capabilities: set[str] = Field(default_factory=set)
    allow_local_fallback: bool = True
    optional: bool = False


class PolicyProfile(BaseModel):
    description: str = ""
    stages: list[PolicyStage] = Field(default_factory=list)


class ProviderCandidate(BaseModel):
    provider: ProviderConfig
    model: ModelConfig
    score: float = 0.0
    reason: str = ""


class ExecutionStage(BaseModel):
    purpose: StagePurpose
    candidates: list[ProviderCandidate]
    required_capabilities: set[str] = Field(default_factory=set)
    allow_local_fallback: bool = True
    optional: bool = False


class ExecutionPlan(BaseModel):
    profile_name: str
    stages: list[ExecutionStage]
    final_selection_strategy: Literal["first_success", "refine_over_draft"] = "first_success"


class RouterRequest(BaseModel):
    request_id: str
    route: Literal["chat_completions", "responses", "embeddings", "completions"]
    model: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    input: Any | None = None
    max_tokens: int | None = None
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    response_format: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = None
    agent_run_id: str | None = None
    node_id: str | None = None
    required_capabilities: set[str] = Field(default_factory=set)
    priority: Priority = Priority.interactive
    local_only: bool = False
    allow_cloud: bool | None = None
    raw_body: dict[str, Any] = Field(default_factory=dict)


class ProviderResponse(BaseModel):
    provider: str
    model: str
    data: dict[str, Any]
    usage: dict[str, int] = Field(default_factory=dict)
    status_code: int = 200


class ProviderHealth(BaseModel):
    provider: str
    ok: bool
    detail: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class QuotaEstimate(BaseModel):
    request_units: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    dimensions: dict[str, int] = Field(default_factory=dict)


class QuotaSnapshot(BaseModel):
    provider: str
    model: str
    dimensions: dict[str, dict[str, int | None]] = Field(default_factory=dict)


class AgentWorkerConfig(BaseModel):
    name: str
    type: str
    command: str
    enabled: bool = False
    quota: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
