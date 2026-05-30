# Neo4j and AssistX Integration Design

## 1. Integration boundary

AssistX is the source of truth for task state, Sophia ingestion, policy decisions, agent runs, service inventory, and artifacts. `auto-router` is an execution-selection service. It reads graph-backed context from AssistX and emits routing outcomes back to AssistX, but it should not become a competing task database.

The reference `auto-assist` architecture currently keeps Sophia and non-realtime task execution aligned around AssistX, Paperclip, and the `hermes_local` adapter. Direct worker/fleet routing remains a follow-up surface, so `auto-router` should integrate as an advisory and routing node without replacing AssistX task ownership.

## 2. Data ownership

| Data | Owner | Router behavior |
|---|---|---|
| Sophia captures and signed voice events | AssistX | Reads policy/context projection only |
| Task lifecycle and dispatch state | AssistX | Receives task IDs and emits route outcomes |
| Agent runs and artifacts | AssistX/Paperclip for current cutover | May attach routing provenance and artifact refs |
| Provider/model capability inventory | AssistX plus router config | Router consumes projection and YAML bootstrap |
| Homelab service inventory | AssistX/Neo4j plus optional scanners | Router renders links and health/status hints |
| Quota reservations and transient counters | auto-router | Stored in Redis/in-memory and summarized to AssistX later |
| Service scan snapshots | auto-router first, AssistX later | Persisted to SQLite and queued into the outbox as graph events |
| Prompt bodies | Caller/router transient memory | Not written to Neo4j by default |

## 3. Context projection contract

AssistX should expose a private or signed context endpoint for the router. The router already supports HTTP-backed context loading through `AUTO_ROUTER_CONTEXT_CONFIG`.

Recommended endpoint:

```text
GET /api/router/context-projection
```

Minimum top-level fields:

| Field | Meaning |
|---|---|
| `revision` | Stable context revision for debugging and dashboard display |
| `source` | Origin of the projection, usually AssistX |
| `generated_at` | Unix timestamp for freshness checks |
| `nodes` | Local machines, LM Studio hosts, Paperclip/Hermes workers, and agent workers |
| `providers` | Provider/model lanes visible to the router |
| `services` | Clickable service URLs and health metadata for the dashboard launchpad |
| `metadata` | Policy revision, burn mode, reserve policy, and rollout flags |

## 4. Node projection

Each node record should describe whether a local or worker node is available and what it can safely do.

| Field | Example | Notes |
|---|---|---|
| `node_id` | `deathstar` | Stable identifier |
| `display_name` | `GPU Specialist (Deathstar)` | Human label for dashboard |
| `lane` | `local` | One of `local`, `free_api`, `paperclip`, `blocked` |
| `running` | `true` | Health/heartbeat result |
| `capabilities` | `chat`, `code`, `low_latency`, `gpu_accelerated` | Used for ranking |
| `services` | list of service records | Node-local URLs such as LM Studio, Neo4j, dashboards, workers |
| `detail` | `LM Studio over Tailscale` | Operator note |

## 5. Provider projection

Each provider record should describe whether a provider can be used and which lane it belongs to.

| Field | Example | Notes |
|---|---|---|
| `provider` | `cerebras` | Must match router provider config name or alias |
| `lane` | `free_api` | Routing lane from AssistX policy |
| `local` | `false` | True for LM Studio/local endpoints |
| `can_use_free_api` | `true` | Whether free quota may be burned |
| `blocked` | `false` | Hard stop when true |
| `node_id` | `cerebras-wse3` | Optional link to local/cloud node |
| `aliases` | `auto/flash-start` | Optional matching names |
| `capabilities` | `chat`, `streaming`, `low_latency` | Used for profile selection |
| `services` | list of service records | Provider URLs such as `/v1`, `/models`, dashboards, docs |
| `detail` | `fast realtime lane` | Dashboard note |

## 6. Service projection

Services are the homelab and cloud URLs the dashboard should expose. They can appear at the top level, inside a node, or inside a provider. The router deduplicates them by `service_id` and renders them as a launchpad.

| Field | Example | Notes |
|---|---|---|
| `service_id` | `deathstar.neo4j.browser` | Stable unique identifier |
| `name` | `Deathstar Neo4j Browser` | Label shown in UI |
| `url` | `http://deathstar-XPS-8920:7474` | Clickable destination |
| `health_url` | `http://deathstar-XPS-8920:7474` | Optional probe target |
| `service_type` | `graph_ui` | Category such as `lmstudio`, `graph_ui`, `router_ui`, `inference_api` |
| `node_id` | `deathstar` | Optional node owner |
| `provider` | `lmstudio-deathstar` | Optional provider owner |
| `status` | `unknown` | `unknown`, `online`, `degraded`, `offline`, or `blocked` |
| `tags` | `neo4j`, `graph`, `browser` | UI filtering and future scanning hints |
| `priority` | `20` | Lower values render first |
| `detail` | `Graph browser on legacy ingest host` | Operator note |

### 6.1 How AssistX should populate services

AssistX can register services from three sources:

1. Static configuration in Neo4j: known services such as Neo4j Browser, AssistX UI, auto-router dashboard, LM Studio endpoints, Paperclip control plane, Redis, and hosted inference APIs.
2. Node heartbeats: each homelab node reports open services it owns, including URL, health URL, service type, and status.
3. Lightweight scanner jobs: optional private-network HTTP/TCP probes update `status`, `last_seen_at`, and `latency_ms` without changing canonical ownership.

### 6.2 Suggested Neo4j service model

```text
(Service {service_id, name, url, health_url, service_type, status, priority, detail})
(SwarmNode)-[:HOSTS_SERVICE]->(Service)
(RouterProvider)-[:EXPOSES_SERVICE]->(Service)
(RouterContextProjection)-[:INCLUDES_SERVICE]->(Service)
```

## 7. Suggested Neo4j model additions

These graph concepts can be added to the `assistx` database or represented through existing nodes if AssistX already has equivalents.

| Node | Purpose | Key properties |
|---|---|---|
| `RouterProvider` | Provider/lane registry | `provider_id`, `lane`, `enabled`, `blocked`, `quota_class` |
| `RouterModel` | Model capability registry | `model_id`, `alias`, `provider_model`, `capabilities`, `context_window` |
| `RouterDecision` | One routing stage decision | `decision_id`, `request_id`, `stage`, `profile`, `status`, `reason` |
| `QuotaSnapshot` | Periodic operational quota state | `quota_id`, `dimension`, `limit`, `used`, `remaining`, `reset_at` |
| `Service` | Service launchpad and health registry | `service_id`, `name`, `url`, `health_url`, `service_type`, `status` |
| `RouterContextProjection` | Published projection revision | `revision`, `generated_at`, `source` |

Recommended relationships in plain language:

- A provider serves one or more router models.
- A local or swarm node exposes one or more providers.
- A node hosts one or more services.
- A provider exposes one or more service URLs.
- A task or agent run used one or more router decisions.
- A router decision selected one provider and one model.
- A provider has periodic quota snapshots.
- A context projection includes the nodes, providers, and services that were visible when generated.

## 8. Event outbox and write-back contract

Router now stores outgoing graph events in a local SQLite outbox before any network write-back. This keeps route execution and service scanning resilient when AssistX is unavailable.

Implemented admin endpoints:

```text
GET  /admin/outbox
POST /admin/outbox/{event_id}/delivered
POST /admin/outbox/{event_id}/failed?error=<message>&retry=true
```

Recommended event types:

| Event type | When emitted |
|---|---|
| `router.execution_stage.completed` | A provider or worker stage succeeds |
| `router.execution_stage.failed` | A provider or worker stage fails |
| `router.execution_stage.skipped` | A provider is skipped because of quota, privacy, circuit, or capability |
| `router.backlog_job.selected` | Scheduler selects a safe backlog job for surplus quota burn |
| `router.quota_snapshot.recorded` | Router publishes quota state for dashboard/history |
| `router.service_snapshot.recorded` | Router records service status/latency from a probe; this is implemented in the local outbox |

### 8.1 Implemented service snapshot payload

Service scans enqueue one `router.service_snapshot.recorded` event per result with an idempotency key:

```text
router.service_snapshot.recorded:<service_id>:<checked_at>:<status>
```

Payload fields:

| Field | Meaning |
|---|---|
| `service_id` | Stable service identifier |
| `name` | Human display name |
| `url` | Probed URL or health URL |
| `status` | `online`, `offline`, `degraded`, `blocked`, or `unknown` |
| `checked_at` | Unix timestamp of the probe |
| `latency_ms` | Probe latency when available |
| `status_code` | HTTP status code when available |
| `error` | Short probe error if failed |
| `skipped` | Whether the scanner skipped the probe |
| `reason` | Skip/failure reason such as external probing disabled |
| `context_revision` | Context revision used during the scan |
| `context_source` | Context source used during the scan |

### 8.2 Execution event payload targets

Future route execution events should include:

| Field | Meaning |
|---|---|
| `request_id` | Router request ID |
| `task_id` | AssistX task ID when available |
| `profile` | Policy profile such as `sophia_realtime` or `backlog_burn` |
| `stage` | Draft/refine/judge/final/agent stage |
| `provider` | Selected provider name |
| `model` | Selected provider model |
| `lane` | Local/free_api/paperclip/blocked |
| `status` | Succeeded, failed, skipped, timeout, unavailable |
| `latency_ms` | Runtime latency |
| `quota_units` | Request/token/neuron/job counters consumed or reserved |
| `privacy_decision` | Local-only, safe-cloud, blocked, or unknown |
| `artifact_refs` | Optional refs to patch, stdout, stderr, summary, or test output |

## 9. Backlog scheduler integration

The backlog scheduler should prefer AssistX APIs over direct graph queries. A future direct Neo4j adapter can be added after the event contract is stable.

Required AssistX capabilities:

- list safe `batch` and `background` tasks;
- filter out local-only or sensitive tasks;
- expose task rank, age, priority, and retry status;
- accept router selected/skipped/completed events;
- attach summaries and artifact refs to the task or agent run.

The router should not automatically mutate repositories or external systems from backlog work. Repo writes, commits, pushes, financial actions, or legal/production side effects require explicit operator approval.

## 10. Security requirements

- Context projection endpoint must be private-network only or signed.
- Service links can be displayed, but service-scanning must remain private-network scoped and rate limited.
- Router event write-back must be idempotent.
- Prompt bodies are not written to Neo4j by default.
- Secrets and `.env` values are never sent to cloud providers.
- Voice authentication and enrollment records are always local-only.
- Backlog burn-down cannot override privacy labels or critical reserves.

## 11. MVP implementation path

1. Keep YAML bootstrap but support `AUTO_ROUTER_CONTEXT_CONFIG=http://assistx:8000/api/router/context-projection`.
2. Expose `/admin/context` and dashboard context cards.
3. Emit local SQLite `usage_events` for every stage.
4. Add service registry rendering from context projection.
5. Add service status scanner as private-network opt-in.
6. Add an outbox table for router events.
7. Enqueue `router.service_snapshot.recorded` events from scans.
8. Add AssistX event sink client with retry and idempotency.
9. Add dry-run backlog scheduler using AssistX APIs.
10. Add Neo4j write-back after AssistX event ingestion is stable.
