# Design Proposal and Implementation Guide: Agentgateway Integration for auto-router

## 1. Executive Summary

This proposal adds `agentgateway` as an optional but first-class gateway sidecar for `auto-router`.

The core design principle is:

```text
auto-router decides what should happen.
agentgateway carries it out safely, observably, and consistently.
```

`auto-router` remains the local-first control plane responsible for:

* Logical model aliases such as `auto/fast`, `auto/code`, `auto/private`, `auto/backlog-burn`, and `auto/flash-start`.
* Free-quota scheduling and burn-down logic.
* Local-only and privacy-sensitive routing decisions.
* AssistX/Neo4j context projection.
* Backlog dry-run and future backlog execution decisions.
* Provider and model eligibility.
* Router usage ledger and provenance outbox.
* Dashboard and operator controls.

`agentgateway` becomes the optional data plane responsible for:

* Normalized OpenAI-compatible provider proxying.
* Provider transport details.
* LLM provider failover and load balancing.
* MCP tool gatewaying.
* Future A2A agent-to-agent connectivity.
* Rate limiting and token budget guardrails.
* Auth, RBAC, TLS, and gateway policy enforcement.
* OpenTelemetry metrics, traces, and logs.

This should be implemented without breaking current direct-provider routing. The final system must support three runtime modes:

```text
direct         -> current auto-router behavior
sidecar        -> auto-router routes through agentgateway
gateway_first  -> generic traffic can hit agentgateway, auto/* still hits auto-router
```

The first implementation target is `sidecar` mode.

---

## 2. Target Architecture

### 2.1 High-Level Topology

```text
Sophia / AssistX / OpenAI-compatible clients / operator tools
        |
        v
+---------------------------------------------------------------+
| auto-router :8088                                             |
|                                                               |
|  Control Plane                                                |
|  - request normalization                                      |
|  - logical alias policy                                       |
|  - privacy/local-only enforcement                             |
|  - quota estimation/reservation                               |
|  - burn-window strategy                                       |
|  - AssistX/Neo4j context projection                           |
|  - provider/model eligibility                                 |
|  - route provenance outbox                                    |
|  - dashboard/operator APIs                                    |
+---------------------------------------------------------------+
        |
        | OpenAI-compatible request with auto-router metadata
        v
+---------------------------------------------------------------+
| agentgateway :3000                                            |
|                                                               |
|  Data Plane                                                   |
|  - OpenAI-compatible provider proxy                           |
|  - provider load balancing/failover                           |
|  - request/response transformations                           |
|  - MCP tool gateway                                           |
|  - future A2A gateway                                         |
|  - token/rate limits                                          |
|  - guardrails                                                 |
|  - auth/RBAC/TLS                                              |
|  - OpenTelemetry metrics/traces/logs                          |
+---------------------------------------------------------------+
        |
        +--> LM Studio / Ollama / vLLM / local endpoints
        +--> Groq / Cerebras / Gemini / OpenRouter / Mistral / other cloud providers
        +--> MCP tool servers
        +--> future A2A agent workers
```

### 2.2 Control/Data Plane Contract

`auto-router` must make decisions before calling `agentgateway`.

`agentgateway` must not override the following `auto-router` decisions:

* `local_only=true`
* `allow_cloud=false`
* `auto/private`
* sensitive markers such as secrets, credentials, voice-auth enrollment, private memory, and local-only task tags
* provider blocks from AssistX/Neo4j context projection
* quota-preserve decisions
* operator-disabled providers

`agentgateway` may handle:

* upstream transport
* route-specific rate limiting
* backend failover
* backend TLS
* request transformations
* provider auth
* metrics/tracing
* MCP/A2A connectivity

---

## 3. Integration Modes

### 3.1 Mode 1: `direct`

This is the current behavior.

```text
client -> auto-router -> direct provider adapter -> provider
```

Use this mode for:

* local development
* debugging provider adapter behavior
* comparing direct vs sidecar latency
* emergency fallback when agentgateway is unavailable

### 3.2 Mode 2: `sidecar`

This is the first target implementation.

```text
client -> auto-router -> agentgateway -> provider
```

Use this mode for:

* normal production routing
* observable LLM gateway traffic
* provider failover
* unified auth/rate-limit layer
* future MCP and A2A connectivity

### 3.3 Mode 3: `gateway_first`

This is a later topology.

```text
generic OpenAI clients -> agentgateway -> generic LLM routes
Sophia / AssistX auto/* clients -> auto-router -> agentgateway
```

Use this mode only after sidecar mode is stable.

---

## 4. Required Repository Changes

Add or update the following files.

```text
docs/
  AGENTGATEWAY_INTEGRATION.md

config/
  agentgateway.local.example.yaml
  agentgateway.gateway.example.yaml
  agentgateway.routes.example.yaml
  providers.example.yaml (addition)
  policies.example.yaml (addition)

src/auto_router/
  gateway.py
  gateway_config.py
  providers.py (modification)
  models.py (if needed)
  policy.py (if needed)
  route_events.py (modification)
  main_live.py (modification)

tests/
  test_agentgateway_config.py
  test_agentgateway_provider.py
  test_agentgateway_privacy.py
  test_agentgateway_headers.py
  test_agentgateway_fallback.py
  fixtures/fake_agentgateway.py

scripts/
  smoke_agentgateway.sh

docker-compose.agentgateway.yml
otel-collector-config.yaml
.env.example (addition)
Makefile (modification)
```

---

## 5. Configuration Design

### 5.1 Environment Variables

Add these to `.env.example`.

```bash
# Agentgateway integration
AUTO_ROUTER_GATEWAY_MODE=sidecar
AUTO_ROUTER_AGENTGATEWAY_ENABLED=true
AUTO_ROUTER_AGENTGATEWAY_BASE_URL=http://agentgateway:3000
AUTO_ROUTER_AGENTGATEWAY_OPENAI_BASE_URL=http://agentgateway:3000/v1
AUTO_ROUTER_AGENTGATEWAY_TIMEOUT_SECONDS=120
AUTO_ROUTER_AGENTGATEWAY_FAIL_OPEN_TO_DIRECT=true
AUTO_ROUTER_AGENTGATEWAY_FAIL_CLOSED_FOR_PRIVATE=true
AUTO_ROUTER_AGENTGATEWAY_EMIT_HEADERS=true
AUTO_ROUTER_AGENTGATEWAY_RECONCILE_USAGE=true

# Observability
AUTO_ROUTER_OTEL_ENABLED=false
AUTO_ROUTER_OTEL_ENDPOINT=http://otel-collector:4317
AGENTGATEWAY_OTEL_ENDPOINT=http://otel-collector:4317

# Local endpoints used behind agentgateway
LM_STUDIO_BASE_URL=http://host.docker.internal:1234/v1
OLLAMA_BASE_URL=http://host.docker.internal:11434/v1

# Hosted provider keys used by agentgateway
GROQ_API_KEY=
CEREBRAS_API_KEY=
GEMINI_API_KEY=
MISTRAL_API_KEY=
OPENROUTER_API_KEY=
```

### 5.2 `config/agentgateway.local.example.yaml`

This should be the minimum local-first sidecar config.

```yaml
# yaml-language-server: $schema=https://agentgateway.dev/schema/config

config:
  tracing:
    otlpEndpoint: "${AGENTGATEWAY_OTEL_ENDPOINT:-http://otel-collector:4317}"
    randomSampling: true

llm:
  port: 3000
  models:
    - name: "*"
      provider: openAI
      params:
        hostOverride: "host.docker.internal:1234"
```

### 5.3 `config/agentgateway.gateway.example.yaml`

This should include local and cloud provider examples. Keep provider keys environment-based.

```yaml
# yaml-language-server: $schema=https://agentgateway.dev/schema/config

config:
  tracing:
    otlpEndpoint: "${AGENTGATEWAY_OTEL_ENDPOINT:-http://otel-collector:4317}"
    randomSampling: true

llm:
  port: 3000

  policies:
    localRateLimit:
      - maxTokens: 200000
        tokensPerFill: 200000
        fillInterval: 24h
        type: tokens

  models:
    - name: "local/*"
      provider: openAI
      params:
        hostOverride: "host.docker.internal:1234"

    - name: "ollama/*"
      provider: openAI
      params:
        hostOverride: "host.docker.internal:11434"

    - name: "groq/*"
      provider: openAI
      params:
        apiKey: "$GROQ_API_KEY"
        hostOverride: "api.groq.com:443"
        pathOverride: "/openai/v1/chat/completions"
      backendTLS: {}

    - name: "mistral/*"
      provider: openAI
      params:
        apiKey: "$MISTRAL_API_KEY"
        hostOverride: "api.mistral.ai:443"
      backendTLS: {}

    - name: "openrouter/*"
      provider: openAI
      params:
        apiKey: "$OPENROUTER_API_KEY"
        hostOverride: "openrouter.ai:443"
        pathOverride: "/api/v1/chat/completions"
      backendTLS: {}
```

### 5.4 `config/providers.example.yaml` Addition

Add `agentgateway` as a normal provider target.

```yaml
providers:
  - id: agentgateway-sidecar
    name: agentgateway sidecar
    kind: openai_compatible
    lane: gateway
    base_url: "${AUTO_ROUTER_AGENTGATEWAY_OPENAI_BASE_URL:-http://agentgateway:3000/v1}"
    api_key_env: AUTO_ROUTER_AGENTGATEWAY_API_KEY
    enabled: true
    gateway_managed: true
    fail_open_to_direct: true
    capabilities:
      - chat
      - responses
      - completions
      - embeddings
      - streaming
      - json
      - tool_calling
    privacy:
      allow_cloud_by_default: false
      respect_local_only: true
      fail_closed_for_private: true
    timeout_seconds: 120
```

### 5.5 Policy Config Addition

Add a routing preference block to `policies.example.yaml`.

```yaml
gateway:
  enabled: true
  mode: sidecar
  provider_id: agentgateway-sidecar
  emit_headers: true
  pass_metadata_in_body: true
  fail_open_to_direct: true
  fail_closed_for_private: true

profiles:
  sophia_realtime:
    gateway_preference: sidecar
    fail_open_to_direct: true
    max_gateway_latency_ms: 1500

  high_priority_deliverable:
    gateway_preference: sidecar
    fail_open_to_direct: true

  backlog_burn:
    gateway_preference: sidecar
    fail_open_to_direct: false

  local_only:
    gateway_preference: direct_or_local_gateway
    allow_cloud: false
    fail_closed_for_private: true

  private:
    gateway_preference: direct_local
    allow_cloud: false
    fail_closed_for_private: true
```

---

## 6. Request Metadata Contract

Every request from `auto-router` to `agentgateway` should include route metadata in headers.

### 6.1 Required Headers

```text
x-auto-router-request-id: <uuid>
x-auto-router-profile: <profile>
x-auto-router-stage: <stage>
x-auto-router-priority: <priority>
x-auto-router-privacy: <public|cloud_allowed|local_only|private|unknown>
x-auto-router-quota-mode: <preserve|balanced|aggressive_burn>
x-auto-router-provider-plan: <provider id or group>
x-auto-router-model-plan: <model id>
x-auto-router-context-revision: <AssistX context revision or none>
```

### 6.2 Optional Headers

```text
x-auto-router-task-id: <AssistX task id>
x-auto-router-agent-run-id: <future AgentRun id>
x-auto-router-node-id: <node id>
x-auto-router-fallback-allowed: true|false
x-auto-router-local-only: true|false
x-auto-router-cloud-allowed: true|false
```

### 6.3 Body Metadata

For gateways that support content-based routing or JSON extraction, also include a top-level metadata block unless it breaks provider compatibility.

```json
{
  "model": "auto/code",
  "messages": [],
  "auto_router": {
    "request_id": "uuid",
    "profile": "high_priority_deliverable",
    "stage": "refine",
    "priority": "repo_critical",
    "privacy": "cloud_allowed",
    "quota_mode": "balanced",
    "context_revision": "assistx:2026-06-04T12:00:00Z",
    "fallback_allowed": true
  }
}
```

If a provider rejects unknown top-level fields, strip `auto_router` before final provider dispatch and rely on headers.

---

## 7. Runtime Routing Flow

### 7.1 Normal Cloud-Allowed Flow

```text
1. Client calls /v1/chat/completions with model=auto/code.
2. auto-router normalizes request.
3. Policy engine maps auto/code -> high_priority_deliverable or code profile.
4. Privacy classifier confirms cloud is allowed.
5. Quota manager reserves provider quota.
6. Provider candidate list is ranked.
7. Gateway mode is sidecar, so auto-router selects agentgateway-sidecar.
8. auto-router emits request headers and optional body metadata.
9. agentgateway routes to selected provider or provider group.
10. Provider returns response.
11. agentgateway returns normalized OpenAI-compatible response.
12. auto-router records usage and latency.
13. auto-router queues route provenance event to outbox.
14. Client receives response.
```

### 7.2 Local-Only Flow

```text
1. Client calls /v1/chat/completions with metadata.local_only=true.
2. auto-router classifies request as local-only.
3. Cloud providers are removed from candidate list.
4. If agentgateway has a local-only route, auto-router may use it.
5. If local gateway route is unavailable, auto-router routes directly to LM Studio.
6. If only cloud gateway providers are available, fail closed.
```

### 7.3 Gateway Unavailable Flow

```text
1. auto-router selects agentgateway-sidecar.
2. agentgateway health check or request fails.
3. auto-router checks fail-open policy.
4. If request is private/local-only: fail closed or fallback to direct local only.
5. If request is cloud-allowed and fail_open_to_direct=true: retry direct provider adapter.
6. Record gateway failure and fallback reason.
7. Queue route failure/fallback event.
```

### 7.4 Backlog Burn Flow

```text
1. AssistX exposes backlog candidates.
2. auto-router dry-run scheduler selects safe candidates.
3. Sensitive/private/local-only tasks are skipped unless local capacity exists.
4. Burn-window strategy determines preserve/balanced/aggressive_burn.
5. auto-router routes selected jobs through auto/backlog-burn.
6. Gateway sidecar handles provider proxying and rate-limit guardrails.
7. auto-router records selected/skipped/executed events.
```

---

## 8. Implementation Plan

### P0 — Documentation and Configuration

#### P0.1 Add integration design doc

Create: `docs/AGENTGATEWAY_INTEGRATION.md`

Include: architecture, modes, config, routing flows, safety rules, implementation tasks, tests, acceptance criteria.

#### P0.2 Add agentgateway config examples

Create:
- `config/agentgateway.local.example.yaml`
- `config/agentgateway.gateway.example.yaml`
- `config/agentgateway.routes.example.yaml`

Acceptance criteria:
- local config can proxy to LM Studio
- gateway config includes local and at least two cloud provider examples
- no secrets committed
- all keys reference environment variables

#### P0.3 Add compose overlay

Create: `docker-compose.agentgateway.yml`

Example structure:
```yaml
services:
  agentgateway:
    image: ghcr.io/agentgateway/agentgateway:latest
    container_name: auto-router-agentgateway
    restart: unless-stopped
    command: ["-f", "/etc/agentgateway/config.yaml"]
    volumes:
      - ./config/agentgateway.local.example.yaml:/etc/agentgateway/config.yaml:ro
    ports:
      - "127.0.0.1:3000:3000"
      - "127.0.0.1:15020:15020"
    environment:
      - GROQ_API_KEY=${GROQ_API_KEY}
      - AGENTGATEWAY_OTEL_ENDPOINT=${AGENTGATEWAY_OTEL_ENDPOINT:-http://otel-collector:4317}
    extra_hosts:
      - "host.docker.internal:host-gateway"

  jaeger:
    image: jaegertracing/all-in-one:latest
    container_name: auto-router-jaeger
    restart: unless-stopped
    ports:
      - "127.0.0.1:16686:16686"
      - "127.0.0.1:14268:14268"
    environment:
      - COLLECTOR_OTLP_ENABLED=true

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.146.0
    container_name: auto-router-otel-collector
    restart: unless-stopped
    volumes:
      - ./otel-collector-config.yaml:/etc/otelcol-contrib/config.yaml:ro
    ports:
      - "127.0.0.1:4317:4317"
    depends_on:
      - jaeger
```

#### P0.4 Add Makefile targets

Add:
- `gateway-up`: Start auto-router + agentgateway + OTEL services
- `gateway-down`: Stop gateway overlay
- `gateway-smoke`: Smoke test gateway endpoint
- `gateway-metrics`: Fetch gateway metrics

---

### P1 — auto-router Gateway Provider Adapter

#### P1.1 Add gateway config model

Create: `src/auto_router/gateway_config.py`

Responsibilities:
- load env vars
- expose gateway mode
- expose base URLs
- expose fail-open/fail-closed settings
- expose header emission settings

Implementation uses dataclass with `from_env()` classmethod.

#### P1.2 Add gateway metadata builder

Create: `src/auto_router/gateway.py`

Responsibilities:
- convert route decision into headers
- add optional body metadata
- strip body metadata if disabled
- prevent cloud routing for private/local-only requests
- build health snapshot for dashboard

#### P1.3 Add `AgentGatewayProviderAdapter`

Modify: `src/auto_router/providers.py`

Add adapter class extending `OpenAICompatibleProviderAdapter`:
- Reuse existing OpenAI-compatible request/response normalization
- Add only gateway-specific header/body metadata layer
- Preserve streaming support
- Preserve response headers (provider, model, profile, stage, latency, gateway used)

#### P1.4 Add gateway health check

Add internal health function:
```python
async def check_agentgateway_health(base_url: str) -> GatewayHealth
```

Expose in:
- `GET /health`
- `GET /admin/services`
- `GET /dashboard`

Dashboard should show: enabled, mode, base_url, metrics_url, fail_open_to_direct, last_error, last_success_at.

---

### P2 — Safety and Privacy Enforcement

#### P2.1 Local-only hard stop

Before gateway dispatch, enforce:
```python
if request.local_only or route_plan.privacy in {"private", "local_only"}:
    if selected_gateway_route_has_cloud_provider:
        raise RoutingDenied("private/local-only request cannot use cloud-backed agentgateway route")
```

Add config: `local_gateway_only=true` for explicitly local gateway routes.

#### P2.2 Sensitive marker detection

Extend tests for:
- metadata.local_only=true
- priority=local_only
- allow_cloud=false
- model=auto/private
- messages containing obvious secrets (api_key, password, ssh private key)

Acceptance criteria:
- sensitive requests never route to cloud-backed agentgateway
- sensitive requests can route to LM Studio directly
- sensitive requests can route to explicitly local-only agentgateway
- gateway failure never fails open to cloud for sensitive requests

#### P2.3 Event safety

Route provenance events must not include prompt bodies or response bodies.

Allowed: request_id, profile, stage, provider, upstream_provider, model, privacy, quota_mode, latency_ms, tokens, fallback_used, gateway_used, status.

Forbidden: messages, prompt, response_text, tool_arguments containing secrets, attachments, raw request/response bodies.

---

### P3 — Usage Reconciliation and Observability

#### P3.1 Usage from responses

When provider responses include usage fields, store in existing usage ledger with additional fields:
- gateway_used BOOLEAN
- gateway_provider TEXT
- gateway_route TEXT
- upstream_provider TEXT
- upstream_model TEXT
- gateway_latency_ms INTEGER
- fallback_used BOOLEAN
- fallback_reason TEXT

#### P3.2 Metrics reconciliation

Add optional scraper/parser for agentgateway metrics endpoint (port 15020).

Config:
```bash
AUTO_ROUTER_AGENTGATEWAY_METRICS_URL=http://agentgateway:15020/metrics
AUTO_ROUTER_AGENTGATEWAY_METRICS_ENABLED=true
```

Dashboard should show: gateway request count, token usage, rate-limit count, error count.

#### P3.3 Trace correlation

All auto-router requests should generate a request ID and pass it to agentgateway via headers:
- x-request-id
- x-auto-router-request-id
- traceparent (when OTEL is enabled)

Acceptance criteria:
- request ID in auto-router logs can be found in gateway logs/traces
- dashboard recent usage shows `gateway_used=true`
- Jaeger traces show the agentgateway span when tracing is enabled

---

### P4 — Content-Based Routing Support

#### P4.1 Header-first routing

Prefer header-based routing for performance and safety. Auto-router emits:
- x-auto-router-priority
- x-auto-router-profile
- x-auto-router-privacy
- x-auto-router-stage

Agentgateway uses these for route selection.

#### P4.2 Body metadata routing

Enable body metadata only for gateway routes that support request transformation. Use for priority/stage/profile/model-family routing. Disable for strict providers or local-only private data.

---

### P5 — MCP Gateway Phase (Future)

After sidecar LLM routing is stable:

#### P5.1 MCP discovery model

Add MCP service records to AssistX/Neo4j context projection with fields: id, kind=mcp, transport, node_id, privacy, tools, enabled, approval_required.

#### P5.2 auto-router MCP policy

Rules:
- MCP discovery is not execution approval
- MCP tools must be explicitly allow-listed
- Private tools must stay local
- Remote tools require per-tool auth policy
- Tool execution must produce provenance events
- Tool arguments must be redacted before logs/events

#### P5.3 Initial MCP targets

Start with low-risk, read-only tools: filesystem-readonly, repo-inspector-readonly, service-status, neo4j-readonly-query, dashboard-status.

---

### P6 — A2A Agent Worker Phase (Future)

After MCP gatewaying and sandbox policy are stable:

#### P6.1 Target future flow

AssistX task -> auto-router backlog scheduler -> auto-router policy/approval -> A2A worker request through agentgateway -> Codex/Gemini/OpenCode worker -> patch/test artifact -> human approval -> optional commit/push -> AssistX/Neo4j provenance write-back.

#### P6.2 Agent worker safety states

discovered, available, review_only, sandbox_write, commit_allowed, push_allowed, blocked_by_policy, blocked_by_quota, blocked_by_missing_approval.

#### P6.3 Initial agent worker permissions

Start with: review_only=true, write_allowed=false, commit_allowed=false, push_allowed=false, network_allowed=false unless explicitly configured.

---

## 9. Test Plan

### 9.1 Unit Tests

Create:
- `tests/test_agentgateway_config.py` — env parsing, default mode, fail-open/closed flags
- `tests/test_agentgateway_headers.py` — required headers, privacy flags, request ID stability
- `tests/test_agentgateway_privacy.py` — local_only hard stop, sensitive marker detection, fail-closed enforcement
- `tests/test_agentgateway_provider.py` — streaming/non-streaming, usage extraction, timeout/429/5xx handling
- `tests/test_agentgateway_fallback.py` — cloud-allowed fallback, backlog-burn no-fallback, local-only fallback

### 9.2 Integration Tests

Add mocked gateway server: `tests/fixtures/fake_agentgateway.py` supporting POST /v1/chat/completions, GET /metrics.

Run: `pytest tests/test_agentgateway_*.py`

### 9.3 Smoke Tests

Create script: `scripts/smoke_agentgateway.sh`

Required checks:
- curl http://localhost:8088/health | jq
- curl http://localhost:3000/ (gateway health)
- curl http://localhost:8088/v1/chat/completions with model=auto/local
- curl http://localhost:15020/metrics | grep agentgateway || true

---

## 10. Dashboard Requirements

Add an `Agentgateway` panel with fields:
- enabled, mode, base_url, openai_base_url, health, last_success_at, last_error
- fail_open_to_direct, fail_closed_for_private, metrics_enabled, otel_enabled
- gateway request count, token count, fallback count, rate-limit count

Recent usage rows should include: gateway_used, gateway_provider, upstream_provider, upstream_model, fallback_used, fallback_reason.

Provider cards should distinguish: direct provider, gateway provider, local gateway route, cloud gateway route, blocked gateway route.

---

## 11. Acceptance Criteria

The implementation is complete when:

1. `make test` passes.
2. `make gateway-up` starts auto-router, agentgateway, and optional OTEL services.
3. `GET /health` shows agentgateway status.
4. `GET /dashboard` shows the agentgateway panel.
5. `POST /v1/chat/completions` with model=auto/local routes through local agentgateway to LM Studio.
6. `POST /v1/chat/completions` with model=auto/code routes through agentgateway when cloud is allowed.
7. metadata.local_only=true never routes to a cloud-backed gateway route.
8. Gateway failure falls back only according to policy.
9. Usage ledger records gateway_used=true when the sidecar is used.
10. Route events include gateway metadata but never include prompt or response bodies.
11. Agentgateway metrics endpoint can be scraped or viewed manually.
12. Existing direct provider routing still works when gateway mode is disabled.

---

## 12. Implementation Agent Prompt

Use the following prompt for Codex or another repo implementation agent:

```text
You are working in the GitHub repository scottjoyner/auto-router.

Goal: Implement agentgateway as an optional first-class sidecar/data-plane integration while preserving current direct-provider routing behavior.

Core architecture:
- auto-router remains the control plane and quota/policy brain.
- agentgateway is an optional data plane for OpenAI-compatible provider proxying, future MCP, future A2A, rate limits, guardrails, and observability.
- Do not replace existing provider routing. Add sidecar mode first.

Required files to add:
- docs/AGENTGATEWAY_INTEGRATION.md
- config/agentgateway.local.example.yaml
- config/agentgateway.gateway.example.yaml
- config/agentgateway.routes.example.yaml
- docker-compose.agentgateway.yml
- otel-collector-config.yaml
- src/auto_router/gateway_config.py
- src/auto_router/gateway.py
- tests/test_agentgateway_config.py
- tests/test_agentgateway_headers.py
- tests/test_agentgateway_provider.py
- tests/test_agentgateway_privacy.py
- tests/test_agentgateway_fallback.py
- scripts/smoke_agentgateway.sh

Required files to update:
- .env.example
- Makefile
- config/providers.example.yaml
- config/policies.example.yaml
- src/auto_router/providers.py
- src/auto_router/route_events.py
- src/auto_router/main_live.py (if needed)

Implementation requirements:
1. Add GatewayConfig loaded from environment.
2. Add gateway mode support: direct, sidecar, gateway_first reserved.
3. Add agentgateway-sidecar provider config support.
4. Add gateway metadata headers (all 12 required + optional).
5. Add optional body metadata under top-level auto_router when enabled.
6. Ensure private/local-only requests cannot route to cloud-backed gateway providers.
7. Ensure fail-open-to-direct never sends private/local-only requests to cloud.
8. Preserve streaming behavior.
9. Preserve non-streaming OpenAI-compatible response shape.
10. Record usage with gateway metadata.
11. Add gateway fields to route events without storing prompt or response bodies.
12. Add health/dashboard visibility for agentgateway.
13. Add tests for config, headers, privacy, provider behavior, and fallback behavior.
14. Add Docker Compose overlay for agentgateway and optional OTEL/Jaeger.
15. Add Makefile smoke targets.

Safety requirements:
- No secrets in repo.
- No prompt bodies in route events.
- No response bodies in route events.
- Sensitive requests fail closed unless an explicitly local-only route exists.
- Existing direct-provider behavior must continue to work when gateway is disabled.

Run:
- python -m py_compile src/auto_router/*.py
- pytest
- new gateway smoke tests when practical

Deliverables: Code implementation, passing tests, updated docs, updated config examples, clear TODO comments only where external dependency prevents full implementation.
```

---

## 13. Suggested Execution Order for the Agent

Use this sequence to reduce risk:

1. Read README.md, docs/HLD.md, docs/LLD.md, providers.py, policy.py, models.py, main_live.py.
2. Add docs and config examples first.
3. Add GatewayConfig.
4. Add gateway header/body metadata helpers.
5. Add mocked tests for helpers.
6. Add AgentGatewayProviderAdapter by extending existing OpenAI-compatible logic.
7. Add privacy/fail-closed enforcement.
8. Add fallback behavior.
9. Add route event metadata.
10. Add dashboard/health fields.
11. Add Docker Compose overlay and Makefile targets.
12. Run py_compile and pytest.
13. Fix regressions.
14. Update docs with actual final behavior.

---

## 14. Non-Goals for This First Pass

Do not implement these in the first pass:

- full A2A execution
- MCP tool execution
- automatic task claiming
- write/commit/push agent workers
- public internet exposure
- Kubernetes controller integration
- provider account/key rotation
- paid-provider spend automation
- prompt body logging
- response body logging

These should remain future phases after sidecar mode is stable.

---

## 15. Final Design Decision

The system should not become: `auto-router replaced by agentgateway`

It should become: `auto-router + agentgateway`

Where:
- auto-router = quota-aware local-first routing brain
- agentgateway = secure observable agentic traffic gateway

That separation keeps the unique value of auto-router while adopting a stronger standardized connectivity layer for LLM, MCP, and future A2A traffic.
