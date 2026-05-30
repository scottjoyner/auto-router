# Cerebras Flash Node Design

## 1. Why this node matters

Cerebras should be treated as a first-class flash-start node in `auto-router`, not just another generic OpenAI-compatible provider. The useful property is speed: it can produce an initial decomposition, task graph, prompt scaffold, file-change plan, or triage summary before slower local or higher-quality refinement lanes finish warming up.

The router should use this node for first-pass planning and safe backlog work, then hand off to local LM Studio, Codex/Gemini/OpenCode workers, or another judge/refine model when the work needs deeper validation.

## 2. Current public model inventory

The current public Cerebras model catalog lists these shared endpoint models:

| Alias in router | Provider model | Intended use |
|---|---|---|
| `cerebras/flash-reasoner` | `gpt-oss-120b` | Production flash reasoning, first-pass plans, code-review scaffolds |
| `cerebras/glm-4.7-preview` | `zai-glm-4.7` | Preview high-capacity reasoning, evaluation, migration testing |

Hosted model availability can change, so the router now includes live provider model discovery through the OpenAI-compatible `/models` endpoint.

## 3. Quota model

The free/trial public limit should be modeled conservatively per listed public model:

| Dimension | Value |
|---|---:|
| Requests per minute | 5 |
| Tokens per minute | 30,000 |
| Tokens per hour | 1,000,000 |
| Tokens per day | 1,000,000 |

The docs note that rate limiting is token-bucket based and can be triggered by whichever dimension is hit first. This means the router should set `max_completion_tokens` tightly for flash-start tasks so it does not over-reserve the entire context window.

## 4. Router role

Node name: `cerebras-wse3`

Provider name: `cerebras`

Lane: `free_api`

Primary capability tags:

- `flash_planning`
- `low_latency`
- `reasoning`
- `code`
- `json`
- `streaming`
- `tool_use` for GLM preview path when supported

## 5. Best uses

### 5.1 Flash-start planning

Use Cerebras as the first stage for:

- instant task decomposition;
- repo implementation outline before a local/Codex pass;
- converting a vague request into a structured execution plan;
- generating a checklist, acceptance criteria, and risk map;
- deciding whether work should become a Sophia task, AssistX task, Paperclip issue, or local-only note.

Suggested flow:

```text
user / Sophia / AssistX request
  -> Cerebras flash-start plan, capped at 512-1500 output tokens
  -> local or stronger refine lane
  -> optional judge pass
  -> final response or task creation
```

### 5.2 Backlog triage

Use Cerebras for quick classification of queued AssistX tasks:

- safe cloud vs local-only;
- likely repo/documentation/test/summarization task;
- estimated complexity;
- suggested worker lane;
- whether the task is valuable enough to spend free quota before reset.

### 5.3 Planner bootstrap for other repos

Other repos can call `auto-router` with `model=auto/flash-start` or `metadata.profile=flash_start_planner` to get a fast plan. The router can return the plan immediately while a slower agent-worker path continues separately through AssistX/Paperclip.

Good candidates:

- `auto-assist`: classify Sophia events and build initial task skeletons;
- `Sophia`: fast response planning and voice command decomposition;
- `auto-ingest`: classify new memory candidates or batch review summaries;
- repo-maintenance agents: generate first-pass TODOs and test plans.

## 6. Guardrails

Cerebras should not be used for:

- voice authentication or enrollment samples;
- secrets, `.env` files, keys, or credentials;
- personal/private documents unless explicitly marked safe-cloud;
- raw memory/transcript data without policy approval;
- irreversible external actions;
- final authority on high-risk legal/financial/production decisions.

The router should always honor `local_only`, `allow_cloud=false`, blocked context projection entries, and critical reserve thresholds.

## 7. Runtime model discovery

The live model discovery path is intentionally separate from router aliases. Static aliases such as `auto/flash-start` and `cerebras/flash-reasoner` remain stable for clients, while live provider inventory can be refreshed and inspected by operators.

Endpoints:

```text
GET  /admin/live-models
POST /admin/live-models/refresh
POST /admin/live-models/refresh?provider=cerebras
```

Deployment entrypoints now use `auto_router.main_live:app`, which wraps the base FastAPI app and registers the live-model admin routes. The console script also targets `auto_router.main_live:run`.

## 8. Implementation status

Implemented:

- `config/providers.example.yaml` includes `cerebras/flash-reasoner` and `cerebras/glm-4.7-preview`.
- `config/context.example.yaml` includes node `cerebras-wse3` and maps the `cerebras` provider to it.
- `config/policies.example.yaml` includes `flash_start_planner` and maps `auto/flash-start` to that profile.
- `PolicyEngine.classify_profile` recognizes `auto/flash-start` and selects `flash_start_planner`.
- Draft-stage scoring boosts models tagged with `flash_planning`.
- `/v1/models` advertises `auto/flash-start` as a logical router alias.
- Dashboard shows flash-start purpose, recent activity, quota highlighting, and usage highlighting.
- `OpenAICompatibleProvider.list_models()` supports live `/models` discovery.
- `LiveModelCache` stores provider discovery results with TTL and error snapshots.
- `auto_router.main_live` registers `/admin/live-models` and refresh endpoints.
- Tests cover flash-start policy selection and live model cache behavior.

Remaining follow-up tasks:

1. Add the live-model table to the dashboard fragment itself.
2. Add scheduler support for `flash_triage_only` mode.
3. Add automatic background refresh cadence for selected volatile providers.

## 9. Example request

```json
{
  "model": "auto/flash-start",
  "messages": [
    {
      "role": "user",
      "content": "Plan the next implementation pass for auto-router and return JSON with tasks, risks, and test commands."
    }
  ],
  "max_completion_tokens": 900,
  "metadata": {
    "priority": "interactive",
    "source": "assistx",
    "allow_cloud": true,
    "profile": "flash_start_planner"
  }
}
```

## 10. Success criteria

- Flash planning returns fast enough to feel interactive.
- Quota use is capped and visible in the dashboard.
- The node helps start planning but does not bypass privacy or task authority.
- AssistX can later see that Cerebras produced a draft plan and another node refined or executed it.
