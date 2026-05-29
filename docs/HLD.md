# High-Level Design: auto-router

## 1. Purpose

`auto-router` is a local containerized LLM routing layer that presents a standard OpenAI/LM Studio-compatible API while scheduling work across free cloud LLM quota, CLI-based agent tools, and local LM Studio endpoints.

The primary optimization target is:

> Maximize useful consumption of legitimate free daily/monthly LLM quota for high-priority deliverables while preserving quality, privacy, reliability, observability, and local fallback.

The system should not just be a cheapest-provider proxy. It should be an orchestration layer that knows when to:

1. draft locally with inexpensive/local models;
2. refine with stronger free-tier models;
3. judge or repair high-value deliverables with a different model/provider;
4. delegate repo-scoped implementation work to CLI agents;
5. fall back to local LM Studio endpoints when external quota is depleted or unsafe to use.

## 2. Key decisions

### Decision 1: split the architecture into two execution planes

`auto-router` will have two coordinated execution planes:

| Plane | Purpose | Examples |
|---|---|---|
| API routing plane | Synchronous OpenAI/LM Studio-compatible request routing | `/v1/chat/completions`, `/v1/responses`, `/v1/embeddings` routed to Gemini API, Groq, Cerebras, Mistral, OpenRouter, Cloudflare, Z.AI, LM Studio |
| Agent worker plane | Repo/task automation using CLI-based coding agents | Codex CLI / Codex plan, Gemini CLI, GitHub Copilot Free/CLI/agent surfaces, OpenCode CLI |

The API routing plane remains the main path for chat, completions, embeddings, and structured-output calls. The agent worker plane is used for longer-running deliverables such as repo implementation, code review, test generation, documentation passes, refactors, and patch validation.

### Decision 2: CLI agents are not treated as generic chat providers

Codex, Gemini CLI, Copilot, and OpenCode are valuable, but they are not all clean drop-in OpenAI-compatible backends. The router will model them as `agent_workers` with job queues, repo sandboxes, prompt templates, budget metadata, and captured outputs instead of forcing them into the same synchronous `/v1/chat/completions` provider interface.

### Decision 3: high-priority deliverables get multi-stage treatment

High-priority deliverables should normally use a local draft plus a stronger free-tier refine or judge pass. This is the default pattern for architecture docs, implementation plans, code reviews, test plans, and repo-wide changes.

### Decision 4: daily free quota should be intentionally consumed

The router should preserve reserve during the day, then aggressively use surplus daily quota before reset on useful work. Surplus work can include code review passes, documentation improvement, test generation, summarization, and low-risk batch enrichment.

### Decision 5: privacy defaults to local for sensitive workloads

Requests tagged `local_only`, matching sensitive patterns, or carrying private files/repos must route only to LM Studio/local tools unless the operator explicitly permits cloud use for that task.

### Decision 6: SQLite + Redis is the MVP persistence stack

MVP uses Redis for atomic quota reservations and SQLite for durable usage/audit data. The schema must be Postgres-compatible so the stack can later move to Redis + Postgres without redesign.

### Decision 7: dashboard is first-class

A small dashboard is required from the first implementation phase. It should show quota remaining, projected burn-down, provider health, agent-worker usage, fallback events, and LM Studio status.

### Decision 8: Neo4j is the context authority, not the router

The router should not invent long-lived context or capability state from request payloads alone. AssistX owns the graph-backed context fabric and publishes the facts the router needs to make safe execution decisions. The router consumes those facts to decide:

- whether a request must stay local;
- whether a provider may spend legitimate free API credits;
- which worker or provider lane is currently allowed;
- what provenance to attach to the response or artifact.

Static YAML remains useful for bootstrap and local development, but the design target is graph-synced context and lane metadata coming from AssistX. The router can point `AUTO_ROUTER_CONTEXT_CONFIG` at AssistX `/api/context/projection` to consume the live snapshot directly.

## 3. Non-goals

- No evasion of provider limits.
- No account/key farming.
- No automated attempts to bypass per-user, per-project, or per-plan quotas.
- No unapproved cloud processing of sensitive data.
- No dependency on a single remote provider.
- No provider-specific client lock-in.
- No assumption that a CLI agent has the same latency or semantics as a chat API.

## 4. Core architecture

```text
OpenAI-compatible clients / local tools / scripts
  -> auto-router FastAPI service
      -> request normalizer
      -> privacy classifier
      -> task + priority classifier
      -> policy engine
      -> execution planner
          -> API routing plane
              -> quota reservation
              -> provider adapter dispatch
              -> response normalizer
          -> agent worker plane
              -> job queue
              -> repo sandbox / worktree
              -> CLI invocation adapter
              -> diff/test/output collector
      -> usage ledger
      -> quota manager
      -> dashboard + metrics
  -> free cloud APIs
  -> CLI coding agents
  -> local LM Studio endpoints
```

## 5. Execution planes

### 5.1 API routing plane

The API routing plane handles standard LLM calls. It should support the API surface commonly expected from LM Studio and OpenAI-compatible clients:

- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/embeddings`
- `POST /v1/completions` where feasible
- streaming Server-Sent Events
- tool/function calling pass-through where supported
- JSON mode / structured-output pass-through where supported
- compatible error envelopes

Initial API providers:

- LM Studio local endpoints
- Groq
- Cerebras
- OpenRouter
- Gemini API
- Mistral API
- Cloudflare Workers AI
- GitHub Models
- Z.AI / Zhipu

### 5.2 Agent worker plane

The agent worker plane handles longer-running software tasks. It does not need to respond inside a single OpenAI-compatible HTTP request unless the job is small and explicitly synchronous.

Initial agent workers:

| Worker | Primary use | Notes |
|---|---|---|
| Codex | implementation passes, repo questions, patch generation, test/debug loops | Treat as premium coding-agent capacity; use for important repo tasks rather than generic chat |
| Gemini CLI | large-context codebase analysis, repo explanation, automated review, multimodal/code tasks | Useful free lane with terminal workflow and non-interactive scripting |
| GitHub Copilot Free / CLI / agent surfaces | focused coding assistance, selected review, IDE/CLI assist | Track limited monthly free usage separately from API provider quota |
| OpenCode CLI | local terminal agent orchestration over configured providers | Useful as a local agent shell and provider-agnostic coding workflow |

Agent workers produce artifacts:

- markdown analysis;
- patch/diff;
- commit hash or branch name;
- test command output;
- lint/typecheck output;
- review notes;
- confidence and failure metadata.

## 6. Quality-optimized high-priority flow

For high-priority deliverables, the router should support multi-stage execution:

```text
Stage 1: local/cheap draft
  -> LM Studio small model, local coding model, or low-cost free API lane

Stage 2: stronger free refine
  -> Gemini / Mistral / Cerebras / Groq / Z.AI / GitHub Models / OpenRouter free model

Stage 3: agent-worker pass when repo/file changes are needed
  -> Codex / Gemini CLI / Copilot / OpenCode

Stage 4: judge/repair when enabled
  -> different provider/model if quota allows

Stage 5: final response or artifact
  -> return best accepted result with provenance metadata
```

This lets cheap local models produce initial structure while scarce free cloud quota and coding-agent capacity are spent on the highest-leverage steps: refinement, critique, tests, implementation, and validation.

## 7. User resource inventory

The initial operator inventory includes:

| Resource | Architectural treatment |
|---|---|
| Free OpenCode CLI instance | Agent worker; useful for local repo tasks and provider-agnostic coding workflows |
| GitHub Copilot Free | Agent/IDE/CLI assist lane; track limited monthly quota separately |
| Gemini CLI Free | Agent worker; large-context repo analysis and code generation lane |
| Codex middle-tier plan | Premium agent worker lane; reserve for high-priority repo implementation/review tasks |
| Free API endpoints | Synchronous routing providers with quota burn-down |
| LM Studio endpoints | Local privacy-preserving draft/fallback providers |

## 8. Request priority classes

| Class | Meaning | Routing behavior |
|---|---|---|
| `critical` | Important operator-facing deliverable | Use local draft, strongest allowed free refine, optional judge, optional agent worker |
| `repo_critical` | Important repo implementation/review task | Use agent worker plan; prefer Codex/Gemini CLI/OpenCode depending on task and quota |
| `interactive` | Normal chat/coding work | Use fast free quota when available, otherwise local |
| `batch` | Offline summarization or enrichment | Schedule around quota burn-down windows and low-priority buckets |
| `background` | Non-urgent maintenance | Run locally unless surplus quota would expire soon |
| `local_only` | Sensitive/private | LM Studio/local tools only |

## 9. Provider classes

### Free cloud API providers

- Gemini for multimodal, long-context, high-quality reasoning where allowed.
- Groq for low-latency fast draft/refine passes.
- Cerebras for fast larger-model inference with token bucket limits.
- Mistral for chat, code, and structured outputs where configured.
- GitHub Models for prototyping and model comparison.
- Cloudflare Workers AI for small edge-style tasks and neuron-budget use.
- Z.AI / Zhipu for GLM flash/free lanes.
- OpenRouter as a comparison/fallback layer for `:free` models.

### Agent providers

- Codex for premium software-engineering agent tasks.
- Gemini CLI for terminal-first codebase analysis, automation, and large-context tasks.
- GitHub Copilot for IDE/CLI assist and review where available.
- OpenCode for local terminal-agent orchestration and provider-flexible repo work.

### Local providers

- LM Studio endpoints on homelab/Tailscale nodes.
- Optional future vLLM/llama.cpp endpoints if they expose an OpenAI-compatible API.

## 10. Quota as a scheduling resource

The router tracks quota by dimension:

- requests per minute
- requests per day
- tokens per minute
- tokens per day
- tokens per month
- neurons per day
- premium requests per month
- CLI/agent request counts
- concurrency
- model-specific context and output caps

Quota reservations happen before dispatch to prevent oversubscription under concurrency. Agent-worker quotas should be modeled separately from API-provider quotas because they may be subscription, monthly, seat-based, or tool-specific rather than token-based.

## 11. Burn-down strategy

The system should use three quota modes:

| Mode | Behavior |
|---|---|
| preserve | keep enough free quota for critical work during prime hours |
| balanced | use free quota when it improves quality or latency |
| aggressive_burn | near reset, spend surplus quota on useful queued work |

Default day policy:

```text
00:00-12:00  preserve critical reserve
12:00-18:00  balanced interactive use
18:00-21:00  release surplus to batch/refine jobs
21:00-reset  aggressive burn-down while protecting final critical reserve
```

Monthly quotas such as Copilot-style or plan-based premium requests should be smoothed across the month instead of burned daily unless a manual override is set.

## 12. Dashboard overview

The dashboard should show:

- remaining quota by provider/model/dimension;
- remaining agent-worker monthly/free usage;
- estimated time to reset;
- today’s burn-down progress;
- projected unused quota at reset;
- high-priority deliverables that used draft/refine/judge/agent stages;
- provider health and open circuit breakers;
- CLI agent availability and last run status;
- LM Studio fallback usage;
- latency/error trends;
- cloud-vs-local split.

## 13. Deployment model

MVP deployment:

```text
llm-router  FastAPI app
redis       atomic reservation counters
sqlite      durable usage ledger
worker      optional background worker process for agent jobs
```

Later deployment:

```text
llm-router
worker pool
redis
postgres
prometheus
grafana
sandbox volume / ephemeral worktrees
optional queue service
```

## 14. Trust boundaries

- Secrets remain in `.env`, Docker secrets, or a future secret manager.
- Sensitive prompts can be forced local.
- Full prompt logging is disabled by default.
- Usage metadata is logged, prompt bodies are redacted unless explicitly enabled.
- CLI agent workers run in controlled worktrees/sandboxes.
- Agent workers require explicit allow-lists for shell commands, network access, and repo write operations.
- The router records what tool/model touched each deliverable.

## 15. Success criteria

- Clients can use `auto-router` exactly like an LM Studio endpoint for standard LLM calls.
- Daily free quota is visibly consumed on meaningful work.
- High-priority jobs receive higher-quality multi-stage treatment.
- Repo tasks can be delegated to configured agent workers with tracked usage and captured outputs.
- Exhausted/unhealthy providers do not break workflows.
- Local LM Studio endpoints always remain available as fallback.
- The dashboard makes quota, quality stages, and fallback behavior obvious.
