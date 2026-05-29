# auto-router

Local-first OpenAI-compatible LLM router for aggressively using legitimate free cloud LLM quota on high-priority deliverables, then falling back to local LM Studio endpoints across the homelab.

## Goal

`auto-router` exposes a local endpoint that looks like a standard LM Studio / OpenAI-compatible server while routing each request through a policy engine:

1. classify task priority, privacy, modality, source, and quality needs;
2. draft with cheap/local models when useful;
3. refine, judge, or repair using the best available free cloud quota for high-priority deliverables;
4. consume daily/monthly free quota intentionally before reset on safe backlog work;
5. support realtime Sophia app requests with low-latency local-first fallback;
6. consume AssistX/Neo4j context projection so routing decisions follow the shared graph state;
7. fail back to LM Studio endpoints when free cloud quota is exhausted, unhealthy, blocked, or disallowed by privacy policy.

## Target API surface

The router should support the same core shape expected by OpenAI-compatible clients and LM Studio workflows:

- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/embeddings`
- `POST /v1/completions` where feasible
- streaming Server-Sent Events for chat/responses
- tool/function calling pass-through where supported
- structured output / JSON schema pass-through where supported
- `GET /health`
- `GET /metrics`
- `GET /dashboard`
- `GET /admin/quota`
- `GET /admin/context`
- `GET /admin/usage`
- `GET /admin/circuits`
- `POST /jobs/agent`

## Design principles

- **Free quota is a resource to schedule.** Daily quotas should be spent on useful work before reset, not accidentally left idle.
- **Sophia realtime traffic is protected.** Realtime app requests get low-latency, privacy-aware routing and should not be starved by backlog burn-down.
- **AssistX owns canonical graph state.** The router consumes context projection from AssistX/Neo4j and writes provenance back; it does not become the task authority.
- **Quality beats raw cost on high-priority deliverables.** Important work can use a local draft plus a stronger free-tier refinement/judge pass.
- **Local-first privacy.** Requests tagged `local_only`, likely sensitive, or matching configured privacy rules bypass cloud providers.
- **Provider terms are respected.** The router is not for account/key rotation to evade limits. It only balances across explicitly configured providers, keys, models, and quotas.
- **LM Studio remains the backstop.** Local OpenAI-compatible endpoints such as `http://r2d2:1234/v1` or `http://deathstar-XPS-8920:1234/v1` should be usable even when every cloud quota is depleted.
- **Dashboard-first operations.** The operator should see remaining quota, burn-down, fallbacks, latency, errors, context revision, circuits, and why a route was chosen.

## Initial provider set

- Google Gemini
- Groq
- Cerebras
- Mistral AI
- GitHub Models
- Cloudflare Workers AI
- Z.AI / Zhipu
- OpenRouter
- Local LM Studio endpoints
- CLI agent workers: Codex, Gemini CLI, GitHub Copilot surfaces, OpenCode

See [`docs/PROVIDER_MATRIX.md`](docs/PROVIDER_MATRIX.md), [`config/providers.example.yaml`](config/providers.example.yaml), and [`config/policies.example.yaml`](config/policies.example.yaml).

## Planned architecture

```text
Sophia realtime app / OpenAI-compatible clients / AssistX / scheduler
  -> auto-router FastAPI container
      -> request normalizer
      -> privacy classifier
      -> task/priority/source classifier
      -> AssistX context projection consumer
      -> quota-aware policy engine
      -> provider and agent-worker adapters
      -> Redis quota reservations
      -> SQLite/Postgres usage ledger
      -> optional AssistX event outbox
      -> dashboard + metrics
  -> free cloud endpoints
  -> CLI coding agents
  -> local LM Studio fallback endpoints
  -> AssistX/Neo4j provenance write-back
```

## Logical model aliases

| Alias | Purpose |
|---|---|
| `auto/fast` | Normal interactive fast-free/local routing |
| `auto/high-quality` | Local draft + stronger free refine/judge |
| `auto/code` | Code-focused local draft + free/cloud refinement |
| `auto/sophia` | Low-latency Sophia realtime profile |
| `auto/backlog-burn` | Controlled surplus-quota burn profile for safe backlog tasks |
| `auto/local` | LM Studio/local-only |
| `auto/private` | LM Studio/local-only with stricter logging/redaction expectations |

## Repo layout

```text
.
├── config/                  # provider, policy, agent-worker, and context examples
├── docs/                    # HLD, LLD, router switching, Neo4j integration, roadmap
├── src/auto_router/         # FastAPI app and routing core
├── tests/                   # unit tests for quota, policy, providers
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── pyproject.toml
└── .env.example
```

## Quick start

```bash
cp .env.example .env
cp config/providers.example.yaml config/providers.yaml
cp config/policies.example.yaml config/policies.yaml
cp config/agent_workers.example.yaml config/agent_workers.yaml

docker compose up --build
```

Then point LM Studio/OpenAI-compatible clients at:

```bash
export OPENAI_BASE_URL=http://localhost:8088/v1
export OPENAI_API_KEY=local-router
```

For AssistX alignment, point the context config to the graph-backed projection endpoint:

```bash
export AUTO_ROUTER_CONTEXT_CONFIG=http://assistx:8000/api/router/context-projection
```

## Documentation

- [`docs/HLD.md`](docs/HLD.md) — high-level design
- [`docs/LLD.md`](docs/LLD.md) — low-level design
- [`docs/ROUTER_SWITCHING_MECHANISM.md`](docs/ROUTER_SWITCHING_MECHANISM.md) — detailed switching design for Sophia, backlog, quota, and fallback routing
- [`docs/NEO4J_ASSISTX_INTEGRATION.md`](docs/NEO4J_ASSISTX_INTEGRATION.md) — graph projection, Neo4j schema, and AssistX provenance write-back
- [`docs/TODO.md`](docs/TODO.md) — prioritized implementation backlog with P0/P1/P2/P3/P4/P5 tasks
- [`docs/IDEAS.md`](docs/IDEAS.md) — future router, backlog, Sophia, and graph ideas
- [`docs/QUOTA_STRATEGY.md`](docs/QUOTA_STRATEGY.md) — quota burn-down and reservation model
- [`docs/PROVIDER_MATRIX.md`](docs/PROVIDER_MATRIX.md) — provider notes and volatile limits
- [`docs/DASHBOARD.md`](docs/DASHBOARD.md) — dashboard design
- [`docs/ALIGNMENT_EVENT.md`](docs/ALIGNMENT_EVENT.md) — Neo4j context and lane contract shared with AssistX
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — deployment setup for AssistX plus router
- [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) — execution plan
- [`docs/SECURITY_PRIVACY.md`](docs/SECURITY_PRIVACY.md) — privacy and key-handling constraints

## Immediate priorities

1. Keep the app importable and remove merge-conflict debris.
2. Prove local-only routing and concrete model alias routing with tests.
3. Add `auto/sophia` and `auto/backlog-burn` policy profiles.
4. Consume AssistX context projection and expose it in `/health`, `/admin/context`, and dashboard.
5. Add AssistX/Neo4j provenance write-back through an outbox.
6. Add a dry-run backlog burn-down scheduler before enabling automated execution.
