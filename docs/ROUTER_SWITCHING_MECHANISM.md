# Router Switching Mechanism Design

## 1. Purpose

`auto-router` is the decision node that decides when work should burn legitimate free LLM quota, stay local, use a repo agent, or fall back to LM Studio. It serves Sophia realtime requests, AssistX/Paperclip task traffic, and backlog burn-down work.

The router is not the long-term task authority. AssistX owns canonical task state in Neo4j. The router owns execution selection, quota reservations, provider health, fallback behavior, and routing provenance.

## 2. Operating modes

| Mode | Trigger | Default behavior | Guardrails |
|---|---|---|---|
| `realtime` | Sophia app and interactive clients | Prefer low-latency local or fast-free models; stream when possible | Never use cloud if `local_only`, voice auth, enrollment, or sensitive context flags are present |
| `critical_deliverable` | manual high-priority request or repo deliverable | Local draft + free refine + optional judge | Preserve final reserve and record provenance |
| `repo_critical` | implementation/review/test work | Agent job path plus optional sync model stages | Worktree sandbox; no write/commit without explicit flags |
| `backlog_burn` | scheduler finds expiring free quota and queued work | Pull safe queued jobs from AssistX and spend surplus | Reserve realtime quota, honor privacy labels, stop on circuit breakers |
| `local_only` | privacy classifier or explicit request | LM Studio/local workers only | No external provider or cloud-backed agent |
| `degraded` | cloud exhausted/unhealthy | Local LM Studio only | Surface fallback reason in metadata/dashboard |

## 3. Inputs used for switching

### 3.1 Request payload inputs

- `model`: concrete provider alias or logical alias such as `auto/sophia`, `auto/high-quality`, `auto/backlog-burn`, or `auto/local`.
- `metadata.priority`: `critical`, `repo_critical`, `interactive`, `batch`, `background`, or `local_only`.
- `metadata.profile`: explicit policy profile override.
- `metadata.local_only`: hard local-only override.
- `metadata.allow_cloud`: hard cloud allow/deny override.
- `metadata.source`: `sophia`, `assistx`, `paperclip`, `cli`, `dashboard`, or `scheduler`.
- `metadata.task_id`, `metadata.event_id`, `metadata.intent_id`: graph provenance references.
- `metadata.privacy`: list of sensitivity labels.

### 3.2 Graph/context projection inputs

AssistX/Neo4j exports a compact context projection consumed by `AUTO_ROUTER_CONTEXT_CONFIG`:

```json
{
  "revision": "assistx-2026-05-29T18:00:00Z",
  "source": "assistx:/api/router/context-projection",
  "nodes": [
    {
      "node_id": "x1-370.lmstudio",
      "lane": "local",
      "running": true,
      "capabilities": ["chat", "code", "low_latency", "gpu_accelerated"]
    }
  ],
  "providers": [
    {
      "provider": "groq",
      "lane": "free_api",
      "can_use_free_api": true,
      "blocked": false,
      "capabilities": ["chat", "low_latency"]
    }
  ],
  "metadata": {
    "reserved_realtime_percent": 25,
    "daily_burn_window_open": true,
    "policy_version": "router-policy-v1"
  }
}
```

The projection is authoritative for live lane state. YAML remains a bootstrap fallback only.

### 3.3 Quota and health inputs

The policy engine and quota manager consider configured rpm/rpd/tpm/tpd/monthly limits, current Redis reservations, reset time, burn-down window, circuit state, provider health, LM Studio loaded inventory, and agent worker availability.

## 4. Switching decision pipeline

```text
Incoming request/job
  -> normalize request
  -> attach graph provenance metadata
  -> classify privacy and risk
  -> choose policy profile
  -> build execution plan
  -> rank candidates by lane, quota, capability, health, latency
  -> reserve quota atomically
  -> dispatch to provider/worker
  -> normalize response/artifact
  -> record usage and provenance
  -> optionally write outcome event back to AssistX
```

## 5. Profile selection rules

1. Explicit `local_only`, `Priority.local_only`, `allow_cloud=false`, or sensitive privacy label always selects `local_only`.
2. Concrete model aliases are honored only when the matching provider is not blocked and privacy allows that lane.
3. Logical aliases select profiles: `auto/sophia -> sophia_realtime`, `auto/fast -> interactive_balanced`, `auto/high-quality -> high_priority_deliverable`, `auto/code -> code_high_quality`, `auto/backlog-burn -> backlog_burn`, `auto/local` or `auto/private -> local_only`.
4. `critical` priority selects `high_priority_deliverable`.
5. `repo_critical` priority selects `code_high_quality` or the agent job path when `metadata.route_to_agent=true`.
6. Scheduler-created backlog jobs select `backlog_burn` only when daily burn window is open and reserve thresholds are satisfied.

## 6. Candidate ranking

Each stage sorts candidate providers by score. Lower score wins. Inputs include provider priority, execution lane from graph projection, model capabilities, required route capabilities, circuit/health state, estimated quota cost, latency class, and stage purpose.

Recommended ranking bias:

```text
sophia_realtime:
  local low-latency model > Groq/Cerebras fast-free > local fallback > blocked

critical_deliverable:
  local draft > high-quality free refine > different-provider judge > local fallback

backlog_burn:
  expiring free quota > edge/free small models > local batch > skip if reserve would be violated

local_only:
  local LM Studio only > no external fallback
```

## 7. Backlog burn-down scheduler

The burn-down scheduler should run every 5-15 minutes. It reads AssistX tasks and quota state, then queues safe work only when there is surplus.

Eligible work includes documentation refinement, test generation, code review comments, non-sensitive summaries, low-risk classification, judge/repair passes, and stale TODO triage.

Never spend cloud/free quota on voice enrollment samples, authentication material, raw private memory/transcripts without approval, secrets, financial/legal/production side effects, or repo writes without explicit approval.

Reserve policy:

```text
remaining_daily_quota <= critical_reserve: no backlog burn
provider circuit open: no backlog burn
Sophia realtime queue above threshold: no backlog burn
privacy label is sensitive: local-only backlog path
reset in burn horizon and reserve satisfied: backlog burn allowed
```

## 8. Neo4j provenance write-back

After every routed stage, the router should emit an event back to AssistX, either directly to `/api/events` or through a durable outbox.

Minimum event fields:

```json
{
  "event_type": "router.execution_stage.completed",
  "source_service": "auto-router",
  "request_id": "...",
  "task_id": "...",
  "stage": "refine",
  "provider": "groq",
  "model": "llama-3.1-8b-instant",
  "lane": "free_api",
  "quota_units": {"requests": 1, "tokens": 2048},
  "status": "succeeded",
  "latency_ms": 812,
  "privacy_decision": "safe_cloud",
  "artifact_refs": []
}
```

AssistX should attach this event to `Task`, `AgentRun`, `PolicyDecision`, and `Artifact` nodes.

## 9. Failure and fallback behavior

| Failure | Required response |
|---|---|
| 429/rate limit | release reservation when no usage occurred, open circuit with retry-after, try next candidate |
| Provider timeout | release reservation, increment failure count, try next candidate |
| Provider 5xx | circuit after threshold, try next candidate |
| No cloud quota | route local if allowed, otherwise return explicit exhausted error |
| Local LM Studio unavailable | try next local endpoint; if none, return degraded error |
| Context projection unavailable | use YAML bootstrap and mark `context_revision=bootstrap` |
| Agent job unavailable | keep job failed with `unavailable`, do not silently reroute to unsafe cloud |

## 10. Dashboard requirements

The dashboard should show context revision/source, local nodes and loaded models, providers by lane, quota remaining and reset estimates, burn-down mode, recent routing decisions, backlog jobs pulled/skipped/completed, circuits, and Sophia local-vs-cloud counters.

## 11. Acceptance criteria

- `auto/sophia` chooses a low-latency, privacy-safe route and falls back locally.
- `auto/backlog-burn` only runs when surplus quota is available and reserve thresholds are met.
- `local_only` requests never touch cloud providers or cloud-backed agent workers.
- The router can ingest AssistX context projection and expose it at `/admin/context`.
- All executed stages write usage to SQLite/Redis-backed ledger and later to AssistX Neo4j.
- Dashboard shows why a provider was selected, skipped, or blocked.
