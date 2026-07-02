# High-Level Design: auto-router Architecture

## 1. Executive summary

`auto-router` is a local-first LLM routing control plane. It exposes an OpenAI/LM Studio-compatible API while selecting between local LM Studio endpoints, free/hosted model providers, Cerebras flash-start planning, dry-run backlog scheduling, service discovery, model registry, agent CLI discovery, and AssistX/Neo4j provenance write-back.

The router is not just a proxy. It is an operations node that decides what can run, where it can run, which quota it may consume, what must stay local, and what provenance should be sent back to AssistX.

Primary goals:

1. Provide a standard local OpenAI-compatible endpoint for Sophia, AssistX, local tools, and LM-compatible clients.
2. Use legitimate free API quota intentionally while protecting realtime/Sophia capacity.
3. Keep local LM Studio endpoints as privacy-preserving fallback.
4. Treat Cerebras as a flash-start planner node for fast decomposition and backlog triage.
5. Discover services, provider models, and agent CLIs without confusing discovery with execution approval.
6. Persist operational telemetry locally and emit durable events to AssistX/Neo4j.
7. Keep backlog and agent execution dry-run/read-only until explicit claim/approval/sandbox flows exist.

## 2. Current system boundary

### In scope

- OpenAI-compatible API routing.
- Policy/profile selection for logical aliases.
- Quota estimation, reservation, usage accounting, and fallback.
- Cerebras WSE-3 flash-start planning lane.
- AssistX/Neo4j context projection consumption.
- Service registry and private-network service scanning.
- Durable provider model registry.
- Host-local agent CLI discovery for Codex, Gemini CLI, and OpenCode.
- Durable event outbox and operator-triggered AssistX dispatch.
- Dry-run backlog selection from manual payloads or read-only AssistX backlog candidates.
- Dashboard/operator console.
- Production Docker Compose / bare-metal deployment support.

### Out of scope for current implementation

- Automatic AssistX task claiming.
- Actual backlog task execution.
- Agent worktree write/commit/push automation.
- Remote node self-report endpoint.
- Background schedulers for scans/model refresh/outbox dispatch.
- Public internet exposure without auth/TLS.
- Provider quota evasion or account/key rotation to bypass limits.

## 3. High-level architecture

```text
Sophia / AssistX / OpenAI-compatible clients / operator dashboard
        |
        v
+---------------------------------------------------------------+
| auto-router FastAPI service                                   |
|                                                               |
|  OpenAI-compatible API                                        |
|    - /v1/models                                               |
|    - /v1/chat/completions                                     |
|    - /v1/responses                                            |
|    - /v1/embeddings                                           |
|    - /v1/completions                                          |
|                                                               |
|  Routing control plane                                        |
|    - request normalization                                    |
|    - policy/profile selection                                 |
|    - privacy/local-only checks                                |
|    - quota estimation/reservation                             |
|    - provider candidate ranking                               |
|    - response/error normalization                             |
|                                                               |
|  Operations plane                                             |
|    - dashboard                                                |
|    - service registry + scanner                               |
|    - durable model registry                                   |
|    - agent CLI discovery                                      |
|    - dry-run backlog scheduler                                |
|    - durable event outbox + AssistX event sink                 |
+---------------------------------------------------------------+
        |                 |                    |
        v                 v                    v
 local LM Studio     free hosted APIs      AssistX/Neo4j
 endpoints           Cerebras/Groq/etc.    context + event sink
        |
        v
 future agent CLIs: Codex / Gemini CLI / OpenCode
```

## 4. Deployment topology

```text
Private LAN / Tailscale

clients / Sophia / AssistX
        |
        v
  auto-router :8088
        |
        +--> Redis :6379
        |       - quota reservations
        |       - transient counters
        |
        +--> SQLite ./data/router.sqlite3
        |       - usage ledger
        |       - service_scan_events
        |       - model_registry_snapshots
        |       - event_outbox
        |
        +--> local LM Studio nodes
        |       - r2d2 / x1-370 / deathstar / other OpenAI-compatible endpoints
        |
        +--> hosted providers
        |       - Cerebras
        |       - Groq
        |       - Gemini
        |       - Mistral
        |       - OpenRouter
        |       - Cloudflare Workers AI
        |       - GitHub Models
        |       - Z.AI
        |
        +--> AssistX
                - context projection
                - read-only backlog candidates
                - idempotent event sink
                - Neo4j behind AssistX
```

Recommended production posture:

- Run `auto_router.main_live:app`.
- Keep admin endpoints private to LAN/Tailscale.
- Keep prompt logging disabled.
- Persist `./data/router.sqlite3` and Redis data.
- Dispatch outbox manually until AssistX event ingestion is stable.
- Keep agent and backlog execution dry-run until approval/sandboxing exists.

## 5. Main runtime modules

| Module | Responsibility |
|---|---|
| `main.py` | Base FastAPI router and OpenAI-compatible routes |
| `main_live.py` | Enhanced production wrapper that registers live-model, service, CLI, backlog, and route-event extensions |
| `models.py` | Shared request, provider, policy, quota, and routing models |
| `policy.py` | Profile selection and route planning |
| `quota.py` | Quota estimation, reservation, release, and snapshots |
| `providers.py` | Provider adapters and OpenAI-compatible dispatch |
| `context.py` | AssistX/Neo4j context projection models |
| `service_scanner.py` | Local/private service reachability scanner |
| `service_store.py` | Durable service scan history |
| `model_registry.py` | Durable hosted provider model registry |
| `live_models.py` | In-memory live model cache with TTL |
| `cli_discovery.py` | Host-local Codex/Gemini/OpenCode discovery |
| `assistx_tasks.py` | Read-only AssistX backlog candidate intake |
| `backlog_scheduler.py` | Dry-run backlog selection and skip/selected provenance |
| `event_outbox.py` | Durable event outbox table and lifecycle states |
| `event_dispatcher.py` | Operator-triggered AssistX event sink dispatcher |
| `route_events.py` | Route execution provenance event creation |
| `route_event_patch.py` | Production wrapper patch around usage recording |

## 6. Request routing flow

```text
Client request
  -> OpenAI-compatible route
  -> normalize into RouterRequest
  -> classify priority / profile / local-only flags
  -> policy engine builds staged plan
  -> candidate providers/models are ranked
  -> quota estimate is computed
  -> quota is reserved when required
  -> provider adapter dispatches request
  -> response is normalized back to OpenAI-compatible shape
  -> usage is recorded
  -> route provenance event is queued into outbox
  -> response returns to client
```

Important behavior:

- Route events do not store prompt bodies or response bodies.
- Provider failures release unused reservations where possible.
- Local LM Studio fallback remains available when cloud quota is depleted, unhealthy, blocked, or disallowed.
- `main_live` adds route-event provenance while the base app stays lean.

## 7. Logical model aliases and policy profiles

| Alias | Primary profile / use |
|---|---|
| `auto/fast` | Normal interactive routing with fast/free/local candidates |
| `auto/flash-start` | Cerebras flash-start planner for instant task decomposition |
| `auto/high-quality` | Local draft plus stronger refine/judge lanes |
| `auto/code` | Code-focused review/planning path |
| `auto/sophia` | Low-latency realtime Sophia profile |
| `auto/backlog-burn` | Controlled safe backlog burn-down selection |
| `auto/local` | Local-only LM Studio routing |
| `auto/private` | Local-only routing for sensitive/private workloads |

The router treats model aliasing as a policy decision, not just a provider map. A request can become a multi-stage plan where stages have different provider classes, capabilities, and quota rules.

## 8. Quota and burn-down architecture

Quota is modeled as a scheduling resource.

Tracked dimensions can include:

- requests per minute;
- requests per day;
- tokens per minute;
- tokens per day;
- tokens per month;
- neurons per day;
- provider-specific limits;
- future CLI/subscription capacity.

Current persistence split:

| Store | Purpose |
|---|---|
| Redis | Atomic reservations and transient counters |
| SQLite usage ledger | Durable request/provider usage history |
| SQLite event outbox | Provenance and integration events |

Quota modes:

| Mode | Purpose |
|---|---|
| `preserve` | Protect realtime/critical capacity |
| `balanced` | Use free quota when it improves quality/latency |
| `aggressive_burn` | Near reset, spend surplus on safe backlog work |

The current backlog scheduler is dry-run only. It checks quota availability without reserving or spending it.

## 9. Cerebras flash-start node

Cerebras is modeled as a first-class flash planning lane, not just another provider row.

Target role:

```text
vague request / Sophia command / AssistX task
  -> Cerebras flash-start plan
  -> local or stronger refine lane
  -> optional judge / human review / future execution
```

Main uses:

- instant task decomposition;
- repo/doc implementation planning;
- backlog triage;
- route selection scaffolding;
- first-pass structured outputs.

Guardrails:

- no secrets;
- no voice-auth or enrollment samples;
- no private-memory raw data;
- no local-only tasks;
- no final authority for irreversible actions.

## 10. AssistX and Neo4j integration

AssistX is the canonical context owner. `auto-router` consumes and emits integration data but does not become the system of record for tasks or graph state.

### Inbound from AssistX

| Endpoint | Purpose |
|---|---|
| `/api/router/context-projection` | Graph-backed context snapshot with nodes, providers, services, and policy facts |
| `/api/router/backlog-candidates` | Read-only backlog candidate list for dry-run selection |

### Outbound to AssistX

| Endpoint | Purpose |
|---|---|
| `/api/events` | Idempotent event sink for router outbox dispatch |

### AssistX route request (2026-06-08)

| Endpoint | Purpose |
|---|---|
| `POST /api/routes/request` | Accept route requests from AssistX, return lane/model/provider decisions |

The route request endpoint accepts a `RouteRequest` with `correlation_id`, `dispatch_id`, `task_id`, `intent`, `context_requirements`, `eligible_lanes`, and `blocked_lanes`. It returns a `RouteDecision` with `lane`, `provider`, `model`, `target_node_id`, `rationale`, and `confidence`.

Lane selection logic:
- `local` lane: selected when task is `local_only` or `sensitive`, or when local provider is available and preferred.
- `free_api` lane: selected when free quota is available and task permits cloud routing.
- `heavy_reasoning` lane: selected when task requires complex reasoning and paid/heavy providers are available.
- `blocked`: returned when no eligible lane matches the request constraints.

Provider selection within a lane:
- Uses the context projection from AssistX to find available providers.
- Prefers providers with higher priority (lower number) in `config/providers.yaml`.
- Falls back to local LM Studio when no free API provider is available.

### Context projection concepts

- nodes;
- providers;
- services;
- provider lanes;
- local/free/blocked policy flags;
- future agent CLI capabilities;
- context revision/source metadata.

### Neo4j concepts targeted by events

- `RouterProvider`;
- `RouterModel`;
- `RouterDecision`;
- `QuotaSnapshot`;
- `Service`;
- `AgentCli`;
- `RouterContextProjection`;
- future `BacklogSelection` / `AgentRun` / `Task` provenance links.

## 11. Service discovery architecture

Service discovery is split into registry and scanning.

```text
AssistX/Neo4j or YAML bootstrap registers services
        -> dashboard renders launchpad URLs
        -> optional scanner probes local/private URLs
        -> scan result persists to SQLite
        -> service snapshot event enters outbox
        -> AssistX/Neo4j can merge status history
```

### Service registry

Known URLs and ownership. Services may be global, node-owned, or provider-owned.

Examples:

- auto-router dashboard;
- auto-router OpenAI API;
- AssistX UI;
- Neo4j Browser/Bolt;
- Redis;
- LM Studio endpoints;
- Paperclip control plane;
- Cerebras/Groq/OpenRouter APIs.

### Service scanner

The scanner probes local/private URLs by default and skips hosted external APIs unless explicitly allowed.

Supported probes:

- HTTP/HTTPS GET;
- TCP check for `bolt://`, `redis://`, and `tcp://`.

Service scan results are stored in SQLite and reflected in the dashboard after refresh.

## 12. Model registry architecture

Hosted provider model inventories can drift. The model registry makes that visible and durable.

```text
operator refreshes provider /models
        -> provider adapter normalizes model records
        -> live model cache updates
        -> durable model_registry_snapshots row is written
        -> startup hydrates latest registry snapshot back into cache
```

Main benefits:

- survive restarts;
- inspect provider drift;
- catch missing/invalid API keys;
- display last-known model inventory during outage;
- later write model snapshots to AssistX/Neo4j.

## 13. Agent CLI discovery architecture

Agent CLIs are discoverable capabilities, not automatically executable workers.

Supported local discovery:

- `codex`;
- `gemini`;
- `opencode`.

Discovery reports:

- installed;
- runnable;
- command path;
- version output;
- node ID;
- credit hint;
- notes/error.

Policy still decides execution state:

```text
missing               -> unavailable
installed+runnable    -> candidate capability
credits exhausted     -> blocked_by_quota
local-only task       -> local node only
write not approved    -> review_only
approval present      -> future write mode
```

Current agent execution status:

- discovery implemented;
- dry-run scheduling implemented;
- execution/write/commit/push not implemented.

## 14. Dry-run backlog architecture

The backlog scheduler is intentionally selection-only.

```text
manual tasks OR AssistX read-only backlog candidates
        -> normalize into BacklogTaskCandidate
        -> filter sensitive/local-only/non-backlog priorities
        -> policy engine creates route plan
        -> quota manager checks availability without reservation
        -> selected/skipped decision created
        -> decision event queued in outbox
```

It does not:

- claim AssistX tasks;
- mutate AssistX;
- call model providers;
- reserve/spend quota;
- run agents;
- modify files or repositories.

Dry-run output supports future approval flows by producing structured selected/skipped decisions and idempotent outbox events.

## 15. Event outbox and provenance architecture

All external write-back is buffered through SQLite before network dispatch.

```text
service scan / CLI discovery / route execution / backlog dry-run
        -> event_outbox pending event
        -> operator-triggered dispatch
        -> AssistX event sink
        -> delivered / retry / dead_letter
        -> future Neo4j merge
```

Implemented event types:

| Event type | Source |
|---|---|
| `router.service_snapshot.recorded` | Service scanner |
| `router.agent_cli.discovered` | CLI discovery |
| `router.execution_stage.completed` | Route execution |
| `router.execution_stage.failed` | Route execution |
| `router.backlog_job.selected` | Dry-run backlog scheduler |
| `router.backlog_job.skipped` | Dry-run backlog scheduler |

Outbox states:

- `pending`;
- `retry`;
- `delivered`;
- `dead_letter`.

The dispatcher treats `2xx` as delivered and `409` as an idempotent duplicate/delivered result.

## 16. Dashboard architecture

The dashboard is the operator console.

Current dashboard responsibilities:

- quota burn-down;
- provider lanes and context alignment;
- service launchpad;
- service scan status counts;
- Cerebras flash-start status;
- node/provider service links;
- recent route usage;
- health and live model refresh controls;
- local service scan control.

Future dashboard additions:

- live model inventory table;
- outbox backlog widget;
- backlog dry-run queue status;
- circuit retry timers;
- local-vs-cloud request split;
- remote node capability view.

## 17. Persistence architecture

| Component | Store | Notes |
|---|---|---|
| Quota reservations | Redis | Atomic counters and TTLs |
| Usage ledger | SQLite | Durable usage history |
| Service scans | SQLite | `service_scan_events` |
| Model registry | SQLite | `model_registry_snapshots` |
| Event outbox | SQLite | Durable integration queue |
| Config bootstrap | YAML | Providers, policies, agents, context |
| Canonical context | AssistX/Neo4j | Long-lived graph state |

SQLite is sufficient for this control-plane stage. The schema is intended to be portable to Postgres later if write volume or multi-instance deployment requires it.

## 18. Security and privacy architecture

Security assumptions:

- private LAN/Tailscale deployment;
- admin endpoints are not public;
- provider keys live in `.env`, Docker secrets, or a future secret manager;
- prompt logging is disabled by default;
- route execution events exclude prompt and response bodies;
- service scanning is local/private by default;
- agent CLI discovery is local/node-trusted only;
- backlog scheduling is dry-run/read-only;
- cloud routing honors `local_only`, `allow_cloud=false`, and privacy metadata.

High-risk categories that must remain local or skipped:

- secrets and credentials;
- voice authentication and enrollment samples;
- private memory/transcripts;
- sensitive files/repos unless explicitly approved;
- irreversible external actions;
- production deployment actions.

## 19. Production operations architecture

Recommended operations loop:

1. Start `auto_router.main_live:app`.
2. Verify `/health`, `/v1/models`, `/admin/context`, `/admin/services`, `/admin/outbox`.
3. Refresh hosted model registry for selected providers.
4. Scan local/private services.
5. Discover host-local agent CLIs.
6. Run dry-run backlog selection.
7. Inspect pending outbox.
8. Dry-run dispatch to AssistX.
9. Dispatch events after AssistX sink is stable.
10. Review dashboard and logs before enabling future background automation.

Key production docs:

- `docs/PRODUCTION_DEPLOYMENT.md`;
- `docs/OPERATOR_RUNBOOK.md`;
- `docs/SERVICE_DISCOVERY.md`;
- `docs/AGENT_SKILLS.md`;
- `docs/NEO4J_ASSISTX_INTEGRATION.md`.

## 20. Future architecture phases

### Phase 1: Current control plane

Implemented:

- OpenAI-compatible router;
- quota-aware routing;
- Cerebras flash-start lane;
- service scanner;
- durable model registry;
- agent CLI discovery;
- dry-run backlog selection;
- event outbox and dispatcher;
- production docs.

### Phase 2: AssistX ownership loop

Implemented (2026-06-08):

- `POST /api/routes/request` endpoint with lane/provider selection and correlation_id passthrough.
- `RouteRequest`, `RouteDecision`, `RouteIntent`, `ContextRequirements` models.
- xwing node and `lmstudio-xwing` provider configuration.

Next:

- Wire outbox dispatcher to POST `router.route_decision` and `router.execution_stage.*` events back to AssistX `/api/events`.
- AssistX task claim/approval flow;
- route/backlog/service/model event ingestion into Neo4j;
- remote node service/CLI self-report;
- dashboard widgets for backlog/outbox/model registry.

### Phase 3: Safe execution plane

Later:

- agent worktree sandbox;
- command allow-list;
- review-only and write-approved modes;
- artifact capture;
- test execution records;
- operator approval before commit/push.

### Phase 4: Autonomous scheduled optimization

Later:

- background service scans with allow-list and jitter;
- background model refresh cadence;
- quota reset-aware burn-down scheduler;
- automatic AssistX event dispatch;
- model/provider drift reports.

## 21. Success criteria

The architecture succeeds when:

1. Clients can use `auto-router` like LM Studio/OpenAI without custom integration.
2. Cloud quota is used intentionally and safely.
3. Sensitive/local-only requests never leak to hosted providers.
4. Local LM Studio fallback keeps workflows alive during provider failure or quota depletion.
5. AssistX/Neo4j can see service status, CLI capabilities, model inventory, route decisions, and backlog selection events.
6. The dashboard gives an operator immediate visibility into routing, quota, services, model inventory, and provenance.
7. Future agent execution can be added behind explicit approval and sandbox controls without redesigning the control plane.
