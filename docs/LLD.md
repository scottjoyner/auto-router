# Low-Level Design: auto-router

## 1. Purpose

This low-level design maps the current `auto-router` architecture to concrete modules, APIs, data models, persistence tables, event payloads, and runtime flows.

It matches the current HLD and production shape:

- OpenAI-compatible routing;
- quota-aware provider selection;
- Cerebras flash-start lane;
- AssistX context projection;
- read-only AssistX backlog intake;
- service registry and scanner;
- durable model registry;
- agent CLI discovery;
- route execution provenance;
- durable event outbox and dispatch;
- dry-run backlog scheduling;
- dashboard/operator endpoints.

## 2. Application entrypoints

### 2.1 Base app

```text
auto_router.main:app
```

The base app owns the core OpenAI-compatible API and routing runtime.

Responsibilities:

- load provider/policy/agent/context config;
- initialize quota manager;
- initialize usage ledger;
- initialize policy engine;
- expose `/v1/*`, health, metrics, dashboard, usage, quota, circuits, context, and agent job routes;
- execute provider dispatch;
- record usage.

### 2.2 Production/enhanced app

```text
auto_router.main_live:app
```

The live wrapper imports the base app and registers production extensions:

```python
install_route_event_patch(main_module)
register_live_model_routes(app, state)
register_service_routes(app, state)
register_cli_routes(app, state)
register_backlog_routes(app, state)
```

Use this entrypoint for production and operator workflows.

## 3. Runtime state object

The base app uses a shared runtime `state` object. Production extensions add attributes lazily.

| Attribute | Owner | Purpose |
|---|---|---|
| `providers` | `main.py` | Provider registry loaded from YAML |
| `policies` | `main.py` | Policy registry loaded from YAML |
| `agents` | `main.py` | Agent worker registry loaded from YAML |
| `context` | `main.py` | AssistX/YAML context snapshot |
| `policy_engine` | `main.py` | Routing/profile planner |
| `quota` | `main.py` | Redis/in-memory quota manager |
| `usage_ledger` | `main.py` | SQLite usage ledger |
| `live_models` | `live_model_routes.py` | In-memory live model cache |
| `model_registry` | `live_model_routes.py` | Durable model registry store |
| `service_status` | `service_routes.py` | In-memory service scan cache |
| `service_store` | `service_routes.py` | Durable service scan store |
| `event_outbox` | service/CLI/backlog/route modules | Durable event queue |
| `cli_discovery` | `cli_routes.py` | Last host-local CLI discovery result |

## 4. API endpoints

### 4.1 OpenAI-compatible endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/v1/models` | List concrete and logical models |
| POST | `/v1/chat/completions` | OpenAI-compatible chat completions |
| POST | `/v1/responses` | OpenAI-compatible responses shim |
| POST | `/v1/embeddings` | OpenAI-compatible embeddings |
| POST | `/v1/completions` | OpenAI-compatible completions |

### 4.2 Core admin/operator endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Router health and context status |
| GET | `/metrics` | Prometheus-style metrics |
| GET | `/dashboard` | Operator dashboard shell |
| GET | `/api/dashboard/summary` | HTMX dashboard summary fragment |
| GET | `/admin/quota` | Quota snapshots |
| GET | `/admin/context` | Current context projection |
| GET | `/admin/usage` | Recent usage events |
| GET | `/admin/circuits` | Circuit breaker state |

### 4.3 Live model/model registry endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/admin/live-models` | In-memory live model cache plus durable registry summary/history |
| POST | `/admin/live-models/refresh` | Refresh all non-LM Studio provider `/models` endpoints |
| POST | `/admin/live-models/refresh?provider=cerebras` | Refresh one provider |

### 4.4 Service registry/scanner endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/admin/services` | Registered services plus latest status/history |
| POST | `/admin/services/scan` | Scan local/private registered services |
| POST | `/admin/services/scan?allow_external=true` | Explicitly include external hosted service URLs |

### 4.5 Agent CLI discovery endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/admin/agent-clis` | Last CLI discovery result |
| POST | `/admin/agent-clis/discover` | Check host-local `codex`, `gemini`, and `opencode` |

### 4.6 Event outbox endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/admin/outbox` | Pending and recent outbox events |
| POST | `/admin/outbox/dispatch` | Dispatch pending events to AssistX |
| POST | `/admin/outbox/dispatch?dry_run=true` | Preview dispatch without state mutation |
| POST | `/admin/outbox/{event_id}/delivered` | Manually mark delivered |
| POST | `/admin/outbox/{event_id}/failed` | Manually mark retry/dead-letter |

### 4.7 Backlog dry-run endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/admin/backlog/assistx/config` | Show AssistX task intake config |
| POST | `/admin/backlog/dry-run` | Dry-run manual backlog task selection |
| POST | `/admin/backlog/dry-run?source=assistx` | Fetch read-only AssistX candidates and dry-run selection |

### 4.8 Agent job endpoint

| Method | Path | Description |
|---|---|---|
| POST | `/jobs/agent` | Existing placeholder/surface for future agent jobs |

Current repo posture: dry-run backlog selection and agent CLI discovery exist; actual agent execution is not enabled.

## 5. Configuration files

| File | Purpose |
|---|---|
| `config/providers.example.yaml` | Provider definitions, models, quota classes, capabilities |
| `config/policies.example.yaml` | Logical profile/stage routing policy |
| `config/agent_workers.example.yaml` | Agent worker definitions, disabled by default |
| `config/context.example.yaml` | Bootstrap AssistX-style context projection, nodes, providers, services |
| `.env.example` | Runtime environment variables |
| `docker-compose.yml` | Container deployment with Redis and persistent data volumes |

## 6. Environment variables

Important variables:

| Variable | Purpose |
|---|---|
| `AUTO_ROUTER_PROVIDER_CONFIG` | Provider YAML path |
| `AUTO_ROUTER_POLICY_CONFIG` | Policy YAML path |
| `AUTO_ROUTER_AGENT_CONFIG` | Agent worker YAML path |
| `AUTO_ROUTER_CONTEXT_CONFIG` | YAML or HTTP AssistX context projection |
| `AUTO_ROUTER_REDIS_URL` | Redis quota/reservation store |
| `AUTO_ROUTER_DATABASE_URL` | SQLite URL |
| `AUTO_ROUTER_LOG_PROMPTS` | Prompt logging flag; should stay false |
| `AUTO_ROUTER_REQUEST_TIMEOUT_SECONDS` | Provider request timeout |
| `AUTO_ROUTER_LIVE_MODEL_CACHE_TTL_SECONDS` | Live model cache TTL |
| `AUTO_ROUTER_ASSISTX_EVENT_SINK_URL` | AssistX event sink endpoint |
| `AUTO_ROUTER_ASSISTX_EVENT_DISPATCH_TIMEOUT_SECONDS` | Outbox dispatch HTTP timeout |
| `AUTO_ROUTER_ASSISTX_EVENT_DISPATCH_MAX_ATTEMPTS` | Dead-letter threshold |
| `AUTO_ROUTER_ASSISTX_TASKS_URL` | Read-only AssistX backlog candidate endpoint |
| `AUTO_ROUTER_ASSISTX_TASKS_TIMEOUT_SECONDS` | AssistX task intake timeout |

Provider keys are loaded from environment variables such as `CEREBRAS_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, and `OPENROUTER_API_KEY`.

## 7. Core request model

`RouterRequest` is the normalized request object used by the policy engine and provider dispatch path.

Key fields:

| Field | Description |
|---|---|
| `request_id` | Unique request ID |
| `route` | `chat_completions`, `responses`, `embeddings`, or `completions` |
| `model` | Requested model or logical alias |
| `messages` | Chat messages for chat route |
| `input` | Responses/embedding input |
| `max_tokens` | Output cap |
| `stream` | Streaming request flag |
| `tools` | Tool/function definitions |
| `response_format` | JSON/structured output hints |
| `metadata` | Caller metadata, profile hints, task IDs |
| `required_capabilities` | Capability requirements |
| `priority` | `critical`, `repo_critical`, `interactive`, `batch`, `background`, `local_only` |
| `local_only` | Hard local-only flag |
| `allow_cloud` | Explicit cloud permission/denial |
| `privacy_labels` | Privacy labels from caller/context |
| `raw_body` | Original body for provider dispatch; excluded from route events |

## 8. Policy planning model

### 8.1 Logical aliases

| Alias | Typical profile |
|---|---|
| `auto/fast` | Interactive fast/free/local profile |
| `auto/flash-start` | `flash_start_planner` |
| `auto/high-quality` | high-priority draft/refine/judge profile |
| `auto/code` | code/repo planning profile |
| `auto/sophia` | `sophia_realtime` |
| `auto/backlog-burn` | `backlog_burn` |
| `auto/local` | local-only |
| `auto/private` | local-only/private |

### 8.2 Policy plan shape

A policy plan contains one or more stages.

Stage fields:

| Field | Description |
|---|---|
| `purpose` | draft/refine/judge/final/agent-style purpose |
| `provider_classes` | Allowed quota/provider classes |
| `required_capabilities` | Required model capabilities |
| `candidate_limit` | Max candidates |
| `fallback_behavior` | Continue/fail semantics |

The planner ranks provider/model candidates by profile, class, capabilities, context projection, provider status, and quota class.

## 9. Provider adapter design

Provider adapter responsibilities:

- build outbound HTTP requests;
- apply provider auth headers;
- pass through supported request fields;
- normalize response/error envelopes;
- extract usage when available;
- expose health and model listing;
- enforce timeout behavior.

OpenAI-compatible providers use a common adapter with provider-specific base URL, key env var, and model map.

LM Studio providers are treated as local OpenAI-compatible endpoints and should be preferred when privacy/local-only policy requires local execution.

## 10. Quota design

### 10.1 QuotaEstimate

Quota estimates include:

| Field | Description |
|---|---|
| `request_units` | Request count cost |
| `input_tokens` | Prompt/input token estimate |
| `output_tokens` | Expected output token estimate |
| `total_tokens` | Estimated total token usage |
| `dimensions` | Provider/model quota dimensions |

### 10.2 Quota operations

Required quota operations:

| Operation | Description |
|---|---|
| `estimate(model, body)` | Estimate request quota cost |
| `can_reserve(provider, model, estimate)` | Check availability without mutation |
| `reserve(provider, model, estimate)` | Reserve quota before dispatch |
| `release(reservation)` | Release unused reservation after failure |
| `record_usage(provider, model, usage)` | Persist actual usage when available |
| `snapshots()` | Return dashboard/admin quota view |

Redis is preferred for atomic reservations. In-memory fallback supports local/dev mode.

## 11. Usage and route provenance

### 11.1 Usage ledger

The base router records usage events in SQLite for dashboard and local history.

Usage includes:

- request ID;
- provider;
- model;
- stage;
- route;
- latency;
- status;
- token usage when available;
- error info when applicable.

### 11.2 Route execution outbox events

The production wrapper patches `_record_usage()` and queues route provenance events.

Event types:

```text
router.execution_stage.completed
router.execution_stage.failed
```

Payload fields:

| Field | Description |
|---|---|
| `request_id` | Router request ID |
| `route` | OpenAI-compatible route |
| `requested_model` | Requested alias/model |
| `priority` | Request priority |
| `profile` | Explicit profile metadata, when present |
| `stage` | Execution stage |
| `provider` | Provider name |
| `model` | Provider model alias |
| `status` | completed/failed |
| `status_code` | HTTP/provider status |
| `latency_ms` | Runtime latency |
| `input_tokens` | Input usage/estimate |
| `output_tokens` | Output usage |
| `total_tokens` | Total usage/estimate |
| `quota_units` | Quota dimensions |
| `local_only` | Local-only flag |
| `allow_cloud` | Cloud permission |
| `stream` | Streaming flag |
| `error_type` | Failure class |
| `error_message` | Short failure detail |
| `context_revision` | Context revision |
| `context_source` | Context source |

Excluded fields:

- raw prompt bodies;
- messages;
- tool payloads;
- response content;
- secrets.

## 12. AssistX context projection

`ContextSnapshot` fields:

| Field | Description |
|---|---|
| `revision` | Projection revision |
| `source` | Source name/URL |
| `generated_at` | Unix timestamp |
| `nodes` | `ContextNode[]` |
| `providers` | `ContextProvider[]` |
| `services` | global `ContextService[]` |
| `metadata` | arbitrary projection metadata |

### 12.1 ContextNode

| Field | Description |
|---|---|
| `node_id` | Stable node ID |
| `display_name` | Operator label |
| `lane` | `local`, `free_api`, `paperclip`, `blocked` |
| `local` | Local node flag |
| `can_use_free_api` | Whether free API lane is available |
| `running` | Node heartbeat/status |
| `capabilities` | Capability tags |
| `detail` | Operator note |
| `services` | Node-owned service links |

### 12.2 ContextProvider

| Field | Description |
|---|---|
| `provider` | Provider name |
| `lane` | Provider lane |
| `local` | Local provider flag |
| `can_use_free_api` | Free API permission |
| `free_api_credits` | Optional credit value |
| `blocked` | Hard block flag |
| `node_id` | Owning node |
| `aliases` | Provider/model aliases |
| `capabilities` | Capability tags |
| `detail` | Operator note |
| `services` | Provider-owned service links |

### 12.3 ContextService

| Field | Description |
|---|---|
| `service_id` | Stable service ID |
| `name` | Display name |
| `url` | Launch URL |
| `service_type` | Service category |
| `node_id` | Owning node |
| `provider` | Owning provider |
| `status` | `unknown`, `online`, `degraded`, `offline`, `blocked` |
| `health_url` | Probe URL |
| `tags` | UI/filter tags |
| `detail` | Operator note |
| `priority` | Sort priority |

## 13. Service registry and scanner

### 13.1 Scanner rules

`service_scanner.py` probes:

| Scheme | Probe |
|---|---|
| `http://` | HTTP GET |
| `https://` | HTTP GET; external skipped unless allowed |
| `bolt://` | TCP connect |
| `redis://` | TCP connect |
| `tcp://` | TCP connect |

Local/private host detection allows:

- localhost;
- loopback;
- RFC1918/private IPs;
- link-local IPs;
- single-label hostnames;
- `.lan` hostnames.

### 13.2 ServiceProbeResult

| Field | Description |
|---|---|
| `service_id` | Service ID |
| `name` | Service name |
| `url` | Probed URL |
| `status` | ServiceStatus |
| `checked_at` | Unix timestamp |
| `latency_ms` | Latency |
| `status_code` | HTTP status |
| `error` | Failure detail |
| `skipped` | Whether skipped |
| `reason` | Skip/failure reason |

## 14. Durable service store

SQLite table: `service_scan_events`.

Columns:

| Column | Type | Notes |
|---|---|---|
| `id` | integer primary key | autoincrement |
| `service_id` | text | indexed with checked_at |
| `name` | text | service name |
| `url` | text | probed URL |
| `status` | text | online/offline/degraded/etc. |
| `checked_at` | integer | unix timestamp |
| `latency_ms` | integer nullable | probe latency |
| `status_code` | integer nullable | HTTP status |
| `error` | text nullable | short error |
| `skipped` | integer | 0/1 |
| `reason` | text nullable | skip/failure reason |

Startup hydrates the latest status into `state.service_status` and merges it into `state.context`.

## 15. Live model cache and durable model registry

### 15.1 LiveModelSnapshot

| Field | Description |
|---|---|
| `provider` | Provider name |
| `ok` | Refresh success flag |
| `fetched_at` | Unix timestamp |
| `expires_at` | Cache expiry |
| `models` | Normalized model records |
| `error` | Error string |
| `stale` | Computed expiry flag |

### 15.2 SQLite table: `model_registry_snapshots`

| Column | Type | Notes |
|---|---|---|
| `id` | integer primary key | autoincrement |
| `provider` | text | indexed with fetched_at |
| `ok` | integer | 0/1 |
| `fetched_at` | integer | unix timestamp |
| `expires_at` | integer | unix timestamp |
| `model_count` | integer | count at refresh |
| `error` | text nullable | refresh error |
| `models_json` | text | normalized model list |

Refresh flow:

```text
POST /admin/live-models/refresh
  -> provider adapter list_models()
  -> LiveModelSnapshot
  -> LiveModelCache.put()
  -> ModelRegistryStore.save_snapshot()
```

Startup flow:

```text
ModelRegistryStore.latest_snapshots()
  -> LiveModelCache.put(snapshot)
```

## 16. Agent CLI discovery

### 16.1 Candidates

| Name | Command | Type |
|---|---|---|
| `codex` | `codex` | `codex` |
| `gemini-cli` | `gemini` | `gemini_cli` |
| `opencode` | `opencode` | `opencode` |

### 16.2 Discovery result

| Field | Description |
|---|---|
| `name` | CLI name |
| `command` | Command checked |
| `type` | CLI type |
| `installed` | Command found on PATH |
| `runnable` | Version check succeeded |
| `path` | Resolved binary path |
| `node_id` | Hostname/node ID |
| `checked_at` | Unix timestamp |
| `version` | First version output line |
| `error` | Error detail |
| `credit_hint` | Manual quota/credit hint |
| `notes` | Operator note |

Discovery emits `router.agent_cli.discovered` events when requested.

## 17. AssistX task intake

### 17.1 Client

`AssistXTaskClient` is read-only.

Configuration:

```text
AUTO_ROUTER_ASSISTX_TASKS_URL=http://assistx:8000/api/router/backlog-candidates
AUTO_ROUTER_ASSISTX_TASKS_TIMEOUT_SECONDS=10
```

Request:

```text
GET <tasks_url>?limit=<n>&queue=backlog&dry_run=true
```

Accepted response shapes:

```json
[{"id": "task-1", "title": "..."}]
```

or:

```json
{"tasks": []}
{"items": []}
{"results": []}
{"backlog": []}
```

### 17.2 Normalized BacklogTaskCandidate

| Field | Description |
|---|---|
| `task_id` | AssistX task ID or fallback |
| `title` | Display title |
| `prompt` | Prompt/description for planning only |
| `model` | default `auto/backlog-burn` |
| `priority` | batch/background preferred |
| `local_only` | Preserved from task/privacy |
| `allow_cloud` | Explicit or derived |
| `sensitive` | Preserved/derived from privacy labels |
| `max_completion_tokens` | Planning cap |
| `metadata` | Includes `assistx_source`, raw status, queue |

Privacy mapping:

| AssistX value | Normalized behavior |
|---|---|
| `privacy=private` | `local_only=true`, `sensitive=true`, `allow_cloud=false` |
| `privacy=secret` | `local_only=true`, `sensitive=true`, `allow_cloud=false` |
| `privacy=voice_auth` | sensitive skip path |
| `privacy=enrollment_sample` | sensitive skip path |

## 18. Dry-run backlog scheduler

### 18.1 Input

`BacklogDryRunRequest`:

| Field | Description |
|---|---|
| `tasks` | Manual task candidates; replaced by AssistX candidates when `source=assistx` |
| `enqueue_events` | Whether to enqueue outbox decisions |
| `preserve_realtime_reserve` | Reserved for future reserve logic |
| `max_tasks` | Optional local cap |

### 18.2 Decision logic

```text
for task in candidates:
  if sensitive -> skipped
  if local_only or allow_cloud is false -> skipped
  if priority not batch/background -> skipped
  build RouterRequest(model=auto/backlog-burn)
  plan = policy_engine.plan(request)
  for each stage candidate:
    estimate = quota.estimate(...)
    if quota.can_reserve(...): selected
  else skipped no eligible provider/model
```

No quota is reserved. No provider is called. No task is claimed.

### 18.3 Decision output

`BacklogDecision`:

| Field | Description |
|---|---|
| `task_id` | Task ID |
| `title` | Title |
| `status` | selected/skipped |
| `reason` | Human-readable reason |
| `profile` | Policy profile |
| `stage` | Selected stage |
| `provider` | Selected provider |
| `model` | Selected model |
| `quota_estimate` | Estimate dict |
| `event_id` | Outbox event ID |

Outbox events:

```text
router.backlog_job.selected
router.backlog_job.skipped
```

## 19. Event outbox

SQLite table: `event_outbox`.

| Column | Type | Notes |
|---|---|---|
| `id` | integer primary key | autoincrement |
| `event_id` | text unique | UUID |
| `event_type` | text | event name |
| `source_service` | text | default `auto-router` |
| `idempotency_key` | text unique | sink dedupe key |
| `payload_json` | text | event payload |
| `status` | text | pending/retry/delivered/dead_letter |
| `attempts` | integer | dispatch attempts |
| `last_error` | text nullable | latest dispatch error |
| `created_at` | integer | unix timestamp |
| `updated_at` | integer | unix timestamp |

### 19.1 Implemented event types

| Event type | Producer |
|---|---|
| `router.service_snapshot.recorded` | service scanner |
| `router.agent_cli.discovered` | CLI discovery |
| `router.execution_stage.completed` | route event patch |
| `router.execution_stage.failed` | route event patch |
| `router.backlog_job.selected` | dry-run scheduler |
| `router.backlog_job.skipped` | dry-run scheduler |

### 19.2 Dispatcher behavior

`AssistXEventDispatcher` reads pending/retry events and POSTs them to `AUTO_ROUTER_ASSISTX_EVENT_SINK_URL`.

Rules:

| Response | Result |
|---|---|
| `2xx` | delivered |
| `409` | delivered/idempotent duplicate |
| `408`, `425`, `429`, `500`, `502`, `503`, `504` | retry unless max attempts reached |
| other non-2xx | dead-letter or retry based on status/max attempts |
| network exception | retry |
| sink not configured | report not configured; do not mutate outbox |
| dry-run | report events; do not mutate outbox |

## 20. Dashboard implementation

Templates:

| Template | Purpose |
|---|---|
| `templates/base.html` | Dark operator shell |
| `templates/dashboard.html` | Hero/actions and HTMX summary loader |
| `templates/fragments/dashboard_summary.html` | Main dashboard telemetry cards |

Dashboard data sources:

- quota snapshots;
- context projection;
- service registry/status;
- provider health;
- agent worker config;
- recent usage;
- Cerebras flash usage heuristic;
- node/provider service links.

Current dashboard controls:

- refresh dashboard;
- refresh Cerebras live models;
- scan local services.

Future controls should include:

- discover agent CLIs;
- run backlog dry-run;
- dispatch outbox dry-run;
- live model inventory table;
- outbox status card.

## 21. Persistence and startup hydration

Startup hydration performed by production route registration:

| Store | Hydration behavior |
|---|---|
| service store | latest scan results populate service cache and context statuses |
| model registry | latest provider snapshots populate live model cache |
| event outbox | no hydration needed; read directly from SQLite |
| usage ledger | read by admin/dashboard endpoints |

## 22. Error handling

| Area | Behavior |
|---|---|
| Provider dispatch error | record usage failure; release reservation when possible; try fallback candidate/stage |
| Live model refresh error | cache and persist error snapshot with short TTL |
| Service scan external URL | skipped unless `allow_external=true` |
| Service scan failure | record offline/error result |
| CLI missing | discovery result installed=false/runnable=false |
| AssistX tasks URL missing | `/admin/backlog/dry-run?source=assistx` returns 400 |
| AssistX event sink missing | dispatch reports not configured and does not mutate outbox |
| Dispatcher transient error | mark retry |
| Dispatcher max attempts | mark dead_letter |

## 23. Security controls

- Prompt logging disabled by default.
- Admin endpoints should be private LAN/Tailscale only.
- Service scanner skips external URLs by default.
- CLI discovery only checks local PATH and version commands.
- Backlog scheduler is dry-run/read-only.
- Route events exclude prompts/responses/raw bodies.
- Agent write/commit/push modes are not implemented.
- Cloud routing must respect `local_only`, `allow_cloud=false`, and privacy metadata.

## 24. Test coverage map

Current tests cover:

| Test file | Area |
|---|---|
| `test_live_models.py` | live model cache and provider model normalization |
| `test_model_registry.py` | durable model registry |
| `test_context_services.py` | service registry helpers |
| `test_service_scanner.py` | service scanner safety behavior |
| `test_service_store.py` | durable service scan store |
| `test_service_routes.py` | service scan merge/outbox behavior |
| `test_event_outbox.py` | outbox state and idempotency |
| `test_event_dispatcher.py` | AssistX event dispatch behavior |
| `test_cli_discovery.py` | CLI discovery outbox behavior |
| `test_route_events.py` | route provenance payloads |
| `test_route_event_patch.py` | live wrapper route-event patch |
| `test_backlog_scheduler.py` | dry-run backlog selection |
| `test_assistx_tasks.py` | AssistX task intake normalization |

Run:

```bash
make smoke
pytest -q
```

## 25. Implementation gaps

Known remaining gaps:

1. AssistX task claim/approval flow.
2. Remote node self-report endpoint for services and CLIs.
3. Model registry write-back events.
4. Dashboard widgets for live model registry, outbox, backlog dry-run, and CLI discovery.
5. Background scan/refresh/dispatch cadence with allow-lists and jitter.
6. Agent worktree sandbox and command allow-list enforcement.
7. Route skipped-event provenance for quota/privacy/circuit skips.
8. Prometheus metrics for service status, model registry, outbox backlog, and backlog selection.
