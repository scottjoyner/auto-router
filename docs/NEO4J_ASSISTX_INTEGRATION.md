# Neo4j and AssistX Integration Design

## 1. Integration boundary

AssistX is the source of truth for task state, Sophia ingestion, policy decisions, agent runs, and artifacts. `auto-router` is an execution-selection service. It reads graph-backed context from AssistX and emits routing outcomes back to AssistX, but it should not become a competing task database.

The reference `auto-assist` architecture currently keeps Sophia and non-realtime task execution aligned around AssistX, Paperclip, and the `hermes_local` adapter. Direct worker/fleet routing remains a follow-up surface, so `auto-router` should integrate as an advisory and routing node without replacing AssistX task ownership.

## 2. Data ownership

| Data | Owner | Router behavior |
|---|---|---|
| Sophia captures and signed voice events | AssistX | Reads policy/context projection only |
| Task lifecycle and dispatch state | AssistX | Receives task IDs and emits route outcomes |
| Agent runs and artifacts | AssistX/Paperclip for current cutover | May attach routing provenance and artifact refs |
| Provider/model capability inventory | AssistX plus router config | Router consumes projection and YAML bootstrap |
| Quota reservations and transient counters | auto-router | Stored in Redis/in-memory and summarized to AssistX later |
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
| `metadata` | Policy revision, burn mode, reserve policy, and rollout flags |

## 4. Node projection

Each node record should describe whether a local or worker node is available and what it can safely do.

| Field | Example | Notes |
|---|---|---|
| `node_id` | `x1-370.lmstudio` | Stable identifier |
| `display_name` | `x1-370 LM Studio` | Human label for dashboard |
| `lane` | `local` | One of `local`, `free_api`, `paperclip`, `blocked` |
| `running` | `true` | Health/heartbeat result |
| `capabilities` | `chat`, `code`, `low_latency`, `gpu_accelerated` | Used for ranking |
| `detail` | `LM Studio over Tailscale` | Operator note |

## 5. Provider projection

Each provider record should describe whether a provider can be used and which lane it belongs to.

| Field | Example | Notes |
|---|---|---|
| `provider` | `groq` | Must match router provider config name or alias |
| `lane` | `free_api` | Routing lane from AssistX policy |
| `local` | `false` | True for LM Studio/local endpoints |
| `can_use_free_api` | `true` | Whether free quota may be burned |
| `blocked` | `false` | Hard stop when true |
| `node_id` | `x1-370.lmstudio` | Optional link to local node |
| `aliases` | `auto/sophia`, `local/default` | Optional matching names |
| `capabilities` | `chat`, `streaming`, `low_latency` | Used for profile selection |
| `detail` | `fast realtime lane` | Dashboard note |

## 6. Suggested Neo4j model additions

These graph concepts can be added to the `assistx` database or represented through existing nodes if AssistX already has equivalents.

| Node | Purpose | Key properties |
|---|---|---|
| `RouterProvider` | Provider/lane registry | `provider_id`, `lane`, `enabled`, `blocked`, `quota_class` |
| `RouterModel` | Model capability registry | `model_id`, `alias`, `provider_model`, `capabilities`, `context_window` |
| `RouterDecision` | One routing stage decision | `decision_id`, `request_id`, `stage`, `profile`, `status`, `reason` |
| `QuotaSnapshot` | Periodic operational quota state | `quota_id`, `dimension`, `limit`, `used`, `remaining`, `reset_at` |
| `RouterContextProjection` | Published projection revision | `revision`, `generated_at`, `source` |

Recommended relationships in plain language:

- A provider serves one or more router models.
- A local or swarm node exposes one or more providers.
- A task or agent run used one or more router decisions.
- A router decision selected one provider and one model.
- A provider has periodic quota snapshots.
- A context projection includes the nodes and providers that were visible when generated.

## 7. Event write-back contract

Router should emit idempotent events to AssistX through the existing event system. The first implementation can use a local outbox table and retry loop before a direct Neo4j writer is added.

Recommended event types:

| Event type | When emitted |
|---|---|
| `router.execution_stage.completed` | A provider or worker stage succeeds |
| `router.execution_stage.failed` | A provider or worker stage fails |
| `router.execution_stage.skipped` | A provider is skipped because of quota, privacy, circuit, or capability |
| `router.backlog_job.selected` | Scheduler selects a safe backlog job for surplus quota burn |
| `router.quota_snapshot.recorded` | Router publishes quota state for dashboard/history |

Minimum payload fields:

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

## 8. Backlog scheduler integration

The backlog scheduler should prefer AssistX APIs over direct graph queries. A future direct Neo4j adapter can be added after the event contract is stable.

Required AssistX capabilities:

- list safe `batch` and `background` tasks;
- filter out local-only or sensitive tasks;
- expose task rank, age, priority, and retry status;
- accept router selected/skipped/completed events;
- attach summaries and artifact refs to the task or agent run.

The router should not automatically mutate repositories or external systems from backlog work. Repo writes, commits, pushes, financial actions, or legal/production side effects require explicit operator approval.

## 9. Security requirements

- Context projection endpoint must be private-network only or signed.
- Router event write-back must be idempotent.
- Prompt bodies are not written to Neo4j by default.
- Secrets and `.env` values are never sent to cloud providers.
- Voice authentication and enrollment records are always local-only.
- Backlog burn-down cannot override privacy labels or critical reserves.

## 10. MVP implementation path

1. Keep YAML bootstrap but support `AUTO_ROUTER_CONTEXT_CONFIG=http://assistx:8000/api/router/context-projection`.
2. Expose `/admin/context` and dashboard context cards.
3. Emit local SQLite `usage_events` for every stage.
4. Add an outbox table for router events.
5. Add AssistX event sink client with retry and idempotency.
6. Add dry-run backlog scheduler using AssistX APIs.
7. Add Neo4j write-back after AssistX event ingestion is stable.
