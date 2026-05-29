# Low-Level Design: auto-router

## 1. API layer

The API layer uses FastAPI and exposes OpenAI/LM Studio-compatible routes while also exposing admin, dashboard, and agent-job endpoints.

### 1.1 Initial endpoints

```text
GET  /health
GET  /metrics
GET  /dashboard
GET  /admin/quota
GET  /admin/providers
GET  /admin/agent-workers
GET  /v1/models
POST /v1/chat/completions
POST /v1/responses
POST /v1/embeddings
POST /v1/completions
POST /jobs/agent
GET  /jobs/agent/{job_id}
GET  /jobs/agent/{job_id}/artifacts
```

### 1.2 Compatibility behavior

- Preserve unknown OpenAI-compatible request fields and pass through when provider supports them.
- Normalize provider-specific response shapes back into OpenAI-compatible output.
- Support streaming with Server-Sent Events.
- Return compatible error envelopes with provider provenance metadata hidden unless debug is enabled.
- Support LM Studio-style local clients without requiring client changes.
- Support model aliases that map to routing profiles rather than one physical model.

### 1.3 Model alias examples

```text
auto/fast              -> fast free provider, then local
auto/high-quality      -> local draft + strongest free refine
auto/code              -> local coder draft + cloud/code refine
auto/repo-agent        -> agent worker job path
auto/local             -> LM Studio only
auto/private           -> LM Studio only with stricter logging redaction
```

### 1.4 AssistX alignment

auto-router should treat AssistX as the context authority when operating in the aligned deployment. The request path may still begin from OpenAI-compatible payloads, but the final routing decision should be informed by a graph-backed registry of:

- local nodes and their current status;
- free API lanes and remaining credit;
- provider capabilities and known limitations;
- privacy flags that force local-only execution;
- agent-worker availability and preferred lanes.

This means the router should be able to answer, in metadata, not just what ran but why it ran there.

## 2. Internal request model

Every synchronous request becomes a `RouterRequest`.

### 2.1 Context inputs

In the aligned deployment, the router should consume context snapshots that identify:

- the request locality policy (`local_only`, `safe_cloud`, or unrestricted);
- the current lane preference (`local`, `free_api`, `paperclip`, `blocked`);
- the provider/model capabilities available for that lane;
- whether the request may spend legitimate free API credits;
- whether an agent worker is available locally or only via a cloud-backed lane.

YAML files remain the bootstrap mechanism, but they should be treated as a projection of graph state, not the system of record.

```python
class RouterRequest(BaseModel):
    request_id: str
    route: Literal[
        "chat_completions",
        "responses",
        "embeddings",
        "completions",
    ]
    model: str | None
    messages: list[dict] = []
    input: Any | None = None
    max_tokens: int | None = None
    stream: bool = False
    tools: list[dict] | None = None
    response_format: dict | None = None
    metadata: dict = {}
    required_capabilities: set[str] = set()
    priority: Literal[
        "critical",
        "repo_critical",
        "interactive",
        "batch",
        "background",
        "local_only",
    ] = "interactive"
    local_only: bool = False
    allow_cloud: bool | None = None
    privacy_labels: set[str] = set()
```

## 3. Execution planes

`auto-router` has two execution planes.

### 3.1 API routing plane

The API routing plane handles synchronous model requests through provider adapters.

```text
RouterRequest
  -> PolicyEngine.plan_sync()
  -> QuotaManager.reserve()
  -> ProviderAdapter.dispatch()
  -> ResponseNormalizer
  -> UsageLedger.record()
```

### 3.2 Agent worker plane

The agent worker plane handles repo-scoped or multi-step tasks using CLI agents. Jobs may run asynchronously and produce artifacts.

```text
AgentJobRequest
  -> AgentPolicyEngine.plan_job()
  -> AgentQuotaManager.reserve()
  -> SandboxManager.prepare_worktree()
  -> AgentWorkerAdapter.run()
  -> ArtifactCollector
  -> optional test/lint runner
  -> UsageLedger.record_agent_event()
```

## 4. Agent job model

```python
class AgentJobRequest(BaseModel):
    job_id: str
    repo_url: str | None = None
    repo_path: str | None = None
    branch: str | None = None
    task: str
    priority: Literal["repo_critical", "critical", "batch", "background"]
    preferred_workers: list[str] = []
    allowed_workers: list[str] = []
    max_runtime_seconds: int = 1800
    allow_write: bool = False
    allow_commit: bool = False
    allow_network: bool = True
    commands_allowlist: list[str] = []
    expected_artifacts: list[str] = []
    metadata: dict = {}

class AgentJobResult(BaseModel):
    job_id: str
    worker_name: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    summary: str | None
    artifacts: list[dict]
    stdout_path: str | None
    stderr_path: str | None
    patch_path: str | None
    commit_sha: str | None
    tests_run: list[dict]
    usage: dict
```

## 5. Provider adapter interface

```python
class ProviderAdapter(Protocol):
    name: str
    provider_type: str

    async def list_models(self) -> list[ModelInfo]: ...
    async def chat_completions(self, request: RouterRequest) -> ProviderResponse: ...
    async def responses(self, request: RouterRequest) -> ProviderResponse: ...
    async def embeddings(self, request: RouterRequest) -> ProviderResponse: ...
    async def completions(self, request: RouterRequest) -> ProviderResponse: ...
    async def health(self) -> ProviderHealth: ...
    def estimate_quota(self, request: RouterRequest) -> QuotaEstimate: ...
    def parse_rate_limit_headers(self, headers: Mapping[str, str]) -> RateLimitSnapshot: ...
```

Adapters implement:

- OpenAI-compatible HTTP transport;
- provider-specific auth and headers;
- error mapping;
- rate-limit header parsing;
- usage extraction;
- response normalization;
- feature support flags for streaming, JSON mode, tools, embeddings, vision, and long context.

## 6. Agent worker adapter interface

```python
class AgentWorkerAdapter(Protocol):
    name: str
    worker_type: Literal["codex", "gemini_cli", "copilot", "opencode", "custom"]

    async def health(self) -> AgentWorkerHealth: ...
    async def estimate_usage(self, job: AgentJobRequest) -> AgentUsageEstimate: ...
    async def run(self, job: AgentJobRequest, sandbox: SandboxContext) -> AgentJobResult: ...
```

### 6.1 Codex worker

Purpose:

- premium implementation pass;
- difficult debugging;
- repo-wide refactor planning;
- final review of critical code changes.

Policy:

- reserve for `repo_critical` and selected `critical` tasks;
- do not use for low-value background work;
- record plan tier/manual quota metadata rather than assuming token-level API accounting.

### 6.2 Gemini CLI worker

Purpose:

- large-context codebase review;
- repo Q&A;
- generated implementation plans;
- documentation and test suggestions.

Policy:

- strong free worker for repo analysis;
- suitable for surplus free-use burn-down if allowed;
- run non-interactively where possible.

### 6.3 GitHub Copilot worker

Purpose:

- focused code assistance;
- selected CLI/IDE-adjacent review;
- patch suggestions where available.

Policy:

- model as monthly constrained quota;
- use for developer productivity and reviews, not generic chat;
- track prompt/job count and manual remaining allowance.

### 6.4 OpenCode worker

Purpose:

- local terminal agent orchestration;
- provider-flexible coding tasks;
- integration with existing OpenCode provider config.

Policy:

- good default worker when provider configuration is already available;
- may be local-only or cloud-backed depending on its provider settings;
- record downstream provider where known.

## 7. Policy engine

The policy engine produces an execution plan rather than a single provider.

```python
class ExecutionStage(BaseModel):
    purpose: Literal["draft", "refine", "judge", "repair", "final"]
    candidates: list[ProviderCandidate]
    required_capabilities: set[str]
    quota_class: str
    allow_local_fallback: bool = True
    max_attempts: int = 1

class AgentStage(BaseModel):
    purpose: Literal["repo_analyze", "implement", "review", "test", "repair"]
    candidates: list[AgentWorkerCandidate]
    allow_write: bool
    allow_commit: bool
    max_runtime_seconds: int

class ExecutionPlan(BaseModel):
    sync_stages: list[ExecutionStage] = []
    agent_stages: list[AgentStage] = []
    final_selection_strategy: Literal[
        "first_success",
        "best_judged",
        "refine_over_draft",
        "agent_artifact",
    ]
```

### 7.1 Example high-priority code answer plan

```text
1. draft: local LM Studio coding model
2. refine: Mistral/Gemini/Cerebras/Groq high-quality free model
3. judge: different provider if quota is available
4. final: return refined answer unless judge rejects it
```

### 7.2 Example repo implementation plan

```text
1. repo_analyze: Gemini CLI or OpenCode
2. implement: Codex or OpenCode depending on priority/quota
3. test: local shell command allow-list
4. review: Gemini CLI/Copilot/Groq/Cerebras judge pass
5. final: return patch summary, artifacts, and next commands
```

## 8. Quota manager

### 8.1 Responsibilities

- Track configured limits.
- Estimate request cost before dispatch.
- Atomically reserve quota.
- Reconcile with actual usage after response.
- Consume expiring quota intentionally for high-priority and batch jobs.
- Open circuit breakers on 429/5xx/timeout patterns.
- Track agent-worker usage separately from API-provider usage.

### 8.2 Quota dimensions

```text
rpm        requests per minute
rpd        requests per day
tpm        tokens per minute
tph        tokens per hour
tpd        tokens per day
tpmth      tokens per month
neurons_d  neurons per day
premium_m  premium requests per month
jobs_d     agent jobs per day
jobs_m     agent jobs per month
conc       concurrency
```

### 8.3 Redis key examples

```text
quota:{provider}:{model}:rpm:{minute_epoch}
quota:{provider}:{model}:rpd:{date}
quota:{provider}:{model}:tpm:{minute_epoch}
quota:{provider}:{model}:tpd:{date}
quota:{provider}:{model}:neurons:{date}
quota:{agent_worker}:jobs_m:{yyyy_mm}
quota:{agent_worker}:premium_m:{yyyy_mm}
circuit:{provider}:{model}
circuit:agent:{agent_worker}
```

### 8.4 Reservation algorithm

```text
estimate units
check all quota dimensions
reserve all dimensions atomically
send request or run job
reconcile usage and headers/manual counters
release unused reservation or mark overage
record event
```

## 9. Burn-down scheduler

The scheduler identifies quota that will expire and raises provider priority for useful work.

Signals:

- reset time within configured horizon;
- quota remaining above target reserve;
- queued batch/background work;
- high-priority deliverables awaiting refinement;
- provider health;
- agent-worker availability;
- monthly quota pace versus target.

Policy example:

```text
If Cloudflare daily neurons reset in < 4h and > 40% remains:
  allow background summarization/classification jobs to consume surplus.

If Groq daily token budget has > 50% remaining after 18:00 local:
  prefer Groq for interactive low-latency refine passes.

If Gemini CLI daily/free usage remains and repo review jobs are queued:
  run repo analysis before daily reset, but do not modify files unless allow_write=true.

If Codex monthly/middle-tier usage is below target pace:
  allow Codex for repo_critical implementation/review tasks.
```

## 10. Data persistence

SQLite is sufficient for bootstrap. Postgres can replace it without changing the domain model.

### 10.1 Tables

```sql
providers(
  id text primary key,
  name text,
  type text,
  base_url text,
  enabled boolean,
  priority integer,
  created_at timestamp,
  updated_at timestamp
);

models(
  id text primary key,
  provider_id text,
  alias text,
  provider_model text,
  capabilities_json text,
  context_window integer,
  enabled boolean
);

agent_workers(
  id text primary key,
  name text,
  worker_type text,
  command text,
  enabled boolean,
  quota_policy_json text,
  sandbox_policy_json text,
  created_at timestamp,
  updated_at timestamp
);

quota_limits(
  id text primary key,
  owner_type text,
  owner_id text,
  dimension text,
  limit_value integer,
  reset_policy_json text
);

usage_events(
  id text primary key,
  request_id text,
  provider_id text,
  model_id text,
  route text,
  priority text,
  input_tokens integer,
  output_tokens integer,
  quota_units_json text,
  status_code integer,
  latency_ms integer,
  error_type text,
  created_at timestamp
);

agent_jobs(
  id text primary key,
  worker_id text,
  repo_url text,
  repo_path text,
  branch text,
  task_hash text,
  priority text,
  status text,
  allow_write boolean,
  allow_commit boolean,
  summary text,
  created_at timestamp,
  started_at timestamp,
  finished_at timestamp
);

agent_artifacts(
  id text primary key,
  job_id text,
  artifact_type text,
  path text,
  sha256 text,
  summary text,
  created_at timestamp
);

execution_stages(
  id text primary key,
  request_id text,
  stage text,
  owner_type text,
  owner_id text,
  model_id text,
  outcome text,
  latency_ms integer,
  created_at timestamp
);

circuit_breakers(
  owner_type text,
  owner_id text,
  state text,
  opened_until timestamp,
  last_error text,
  updated_at timestamp
);
```

## 11. Dashboard implementation

MVP dashboard is server-rendered HTML from FastAPI:

- `/dashboard` returns a simple page;
- page polls `/admin/quota`, `/health`, and `/admin/agent-workers`;
- no frontend build chain required for phase 1.

Dashboard cards:

- provider health;
- API quota remaining;
- agent-worker quota remaining;
- daily burn-down progress;
- monthly usage pace;
- queued/running agent jobs;
- recent high-priority stage history;
- LM Studio endpoint status;
- circuit breakers.

Later:

- React or HTMX UI;
- Prometheus/Grafana integration;
- historical burn-down graphs;
- manual provider/worker enable-disable controls;
- job artifact browser.

## 12. Local LM Studio discovery

Static config first:

```yaml
local_fallbacks:
  - name: lmstudio-r2d2
    base_url: http://r2d2:1234/v1
  - name: lmstudio-deathstar
    base_url: http://deathstar-XPS-8920:1234/v1
```

Future discovery:

- configured LAN/Tailscale CIDR scan;
- `/v1/models` probing;
- benchmark loop;
- loaded-model inventory;
- OpenCode config export;
- endpoint health scoring.

## 13. Sandbox and command execution

Agent workers run in controlled directories.

MVP policy:

- clone/copy repo into an ephemeral worktree;
- do not write to the canonical repo unless explicitly enabled;
- allow only configured commands;
- capture stdout/stderr;
- store patch files before commit;
- require explicit `allow_commit=true` before committing;
- require explicit branch configuration before pushing.

Recommended command allow-list examples:

```yaml
commands_allowlist:
  - pytest
  - ruff check
  - mypy
  - npm test
  - npm run lint
  - pnpm test
  - docker compose config
```

## 14. Configuration files

### 14.1 `providers.yaml`

Defines API providers, models, capabilities, quota dimensions, and fallback priority.

### 14.2 `agent_workers.yaml`

Defines CLI agents.

```yaml
agent_workers:
  - name: codex
    type: codex
    command: codex
    enabled: true
    quota:
      dimension: premium_m
      mode: manual_monthly
    policy:
      allowed_priorities: [repo_critical, critical]
      allow_write_default: false

  - name: gemini-cli
    type: gemini_cli
    command: gemini
    enabled: true
    quota:
      dimension: jobs_d
      mode: manual_daily
    policy:
      allowed_priorities: [repo_critical, critical, batch]

  - name: opencode
    type: opencode
    command: opencode
    enabled: true
    policy:
      allowed_priorities: [repo_critical, critical, batch, background]
```

### 14.3 `policies.yaml`

Defines model aliases and multi-stage plans.

## 15. Testing strategy

### 15.1 Unit tests

- Quota reservations.
- Routing policy decisions.
- Privacy classification.
- Model alias resolution.
- Agent worker selection.
- Circuit breaker state transitions.

### 15.2 Contract tests

- OpenAI-compatible request/response envelopes.
- Streaming SSE format.
- Embedding response shape.
- Error envelope compatibility.

### 15.3 Integration tests

- LM Studio with `OPENAI_BASE_URL=http://localhost:1234/v1`.
- Mock external providers returning 429/5xx.
- Mock CLI agent command producing patch artifacts.
- Dashboard JSON endpoints.

### 15.4 Acceptance criteria

- A standard OpenAI-compatible client can use `/v1/chat/completions` without knowing a router is present.
- A high-priority request can execute draft + refine stages.
- A repo-critical job can queue an agent worker and capture artifacts.
- Exhausted quota falls back cleanly.
- Local-only requests never touch cloud or cloud-backed agent workers.
