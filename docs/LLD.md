# Low-Level Design: auto-router

## 1. API layer

The API layer uses FastAPI and exposes OpenAI-compatible routes.

### Initial endpoints

```text
GET  /health
GET  /metrics
GET  /dashboard
GET  /admin/quota
GET  /v1/models
POST /v1/chat/completions
POST /v1/responses
POST /v1/embeddings
```

### Compatibility behavior

- Preserve unknown OpenAI-compatible request fields and pass through when provider supports them.
- Normalize provider-specific response shape back into OpenAI-compatible output.
- Support streaming with Server-Sent Events in a later phase.
- Return compatible error envelopes with provider provenance metadata hidden unless debug is enabled.

## 2. Request model

Internally, every request becomes a `RouterRequest`:

```python
class RouterRequest(BaseModel):
    route: str
    model: str | None
    messages: list[dict] = []
    input: Any | None = None
    max_tokens: int | None = None
    stream: bool = False
    metadata: dict = {}
    required_capabilities: set[str] = set()
    priority: str = "interactive"
    local_only: bool = False
```

## 3. Provider adapter interface

```python
class ProviderAdapter(Protocol):
    name: str
    async def list_models(self) -> list[ModelInfo]: ...
    async def chat_completions(self, request: RouterRequest) -> ProviderResponse: ...
    async def embeddings(self, request: RouterRequest) -> ProviderResponse: ...
    async def health(self) -> ProviderHealth: ...
```

Adapters should implement:

- OpenAI-compatible HTTP transport;
- provider-specific headers;
- error mapping;
- rate-limit header parsing;
- usage extraction;
- response normalization.

## 4. Policy engine

The policy engine produces an execution plan rather than a single provider.

```python
class ExecutionStage(BaseModel):
    purpose: Literal["draft", "refine", "judge", "final"]
    candidates: list[ProviderCandidate]
    required_capabilities: set[str]
    quota_class: str
    allow_local_fallback: bool = True

class ExecutionPlan(BaseModel):
    stages: list[ExecutionStage]
    final_selection_strategy: Literal["first_success", "best_judged", "refine_over_draft"]
```

### Example high-priority code plan

```text
1. draft: local LM Studio coding model
2. refine: Mistral/Gemini/Cerebras/Groq high-quality free model
3. judge: different provider if quota is available
4. final: return refined answer unless judge rejects it
```

## 5. Quota manager

### Responsibilities

- Track configured limits.
- Estimate request cost before dispatch.
- Atomically reserve quota.
- Reconcile with actual usage after response.
- Consume expiring quota intentionally for high-priority and batch jobs.
- Open circuit breakers on 429/5xx/timeout patterns.

### Redis key examples

```text
quota:{provider}:{model}:rpm:{minute_epoch}
quota:{provider}:{model}:rpd:{date}
quota:{provider}:{model}:tpm:{minute_epoch}
quota:{provider}:{model}:tpd:{date}
quota:{provider}:{model}:neurons:{date}
circuit:{provider}:{model}
```

### Reservation algorithm

```text
estimate units
check all quota dimensions
reserve all dimensions atomically
send request
reconcile usage and headers
release unused reservation or mark overage
```

## 6. Burn-down scheduler

The scheduler identifies quota that will expire and raises provider priority for useful work.

Signals:

- reset time within configured horizon;
- quota remaining above target reserve;
- queued batch/background work;
- high-priority deliverables awaiting refinement;
- provider health.

Policy example:

```text
If Cloudflare daily neurons reset in < 4h and > 40% remains:
  allow background summarization/classification jobs to consume surplus.

If Groq daily token budget has > 50% remaining after 18:00 local:
  prefer Groq for interactive low-latency refine passes.
```

## 7. Data persistence

SQLite is sufficient for bootstrap. Postgres can replace it without changing the domain model.

### Tables

```sql
providers(id, name, type, base_url, enabled, priority, created_at, updated_at)
models(id, provider_id, alias, provider_model, capabilities_json, enabled)
quota_limits(id, provider_id, model_id, dimension, limit_value, reset_policy_json)
usage_events(id, request_id, provider_id, model_id, route, priority, input_tokens, output_tokens, quota_units_json, status_code, latency_ms, error_type, created_at)
execution_stages(id, request_id, stage, provider_id, model_id, outcome, latency_ms, created_at)
circuit_breakers(provider_id, model_id, state, opened_until, last_error, updated_at)
```

## 8. Dashboard implementation

MVP dashboard can be server-rendered HTML from FastAPI:

- `/dashboard` returns a simple page;
- page polls `/admin/quota` and `/health`;
- no frontend build chain required for phase 1.

Later:

- React or HTMX UI;
- Prometheus/Grafana integration;
- historical burn-down graphs.

## 9. Local LM Studio discovery

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
- OpenCode config export.

## 10. Testing strategy

- Unit-test quota reservations.
- Unit-test routing policy decisions.
- Mock provider 429/5xx and verify fallback.
- Contract-test OpenAI-compatible request/response envelopes.
- Integration-test with LM Studio using `OPENAI_BASE_URL=http://localhost:1234/v1`.
