# auto-router

Local-first OpenAI-compatible router for model requests, free-quota burn-down, service discovery, model registry, and agent capability orchestration across the homelab.

`auto-router` exposes a local endpoint that looks like a standard LM Studio / OpenAI-compatible server while routing each request through policy, quota, context, and privacy controls. It is designed to sit between Sophia, AssistX, local LM Studio endpoints, hosted free-tier providers, service URLs, and code-agent CLIs.

## What it does now

- Serves OpenAI-compatible endpoints for chat, responses, embeddings, completions, and model listing.
- Routes logical aliases such as `auto/fast`, `auto/high-quality`, `auto/code`, `auto/sophia`, `auto/backlog-burn`, `auto/flash-start`, `auto/local`, and `auto/private`.
- Adds Cerebras as a WSE-3 flash-start planning lane through `auto/flash-start`.
- Tracks quota reservations and usage with Redis plus a local SQLite ledger.
- Reads AssistX/Neo4j-style context projection for providers, nodes, services, and lane policy.
- Renders an operations dashboard with quota burn-down, provider lanes, service launchpad, Cerebras flash-start status, and recent usage.
- Discovers hosted provider live models through `/admin/live-models` and `/admin/live-models/refresh`.
- Persists model registry snapshots so provider inventory survives restarts and exposes drift/history data.
- Registers service URLs, scans local/private services, persists scan history, and updates service status in the dashboard.
- Discovers host-local agent CLIs: Codex, Gemini CLI, and OpenCode.
- Reads AssistX backlog candidates for dry-run scheduling without claiming, mutating, or executing tasks.
- Queues service, model, agent discovery, backlog dry-run, and route execution provenance into a durable SQLite outbox for AssistX/Neo4j write-back.
- Dispatches pending outbox events to a configured AssistX event sink with retry/dead-letter handling.

## Design principles

- **Free quota is a resource to schedule.** Daily quotas should be spent on useful work before reset, not accidentally left idle.
- **Sophia realtime traffic is protected.** Realtime app requests get low-latency, privacy-aware routing and should not be starved by backlog burn-down.
- **AssistX owns canonical graph state.** The router consumes context projection from AssistX/Neo4j and queues provenance events back; it does not become the task authority.
- **Discovery is not policy.** A CLI, service, hosted model, or backlog task can be visible while still blocked by credits, safety, or operator policy.
- **Dry-run before execution.** Backlog scheduling currently selects/skips only; it does not spend quota, call providers, claim tasks, or run agents.
- **Local-first privacy.** Requests tagged `local_only`, likely sensitive, or matching configured privacy rules bypass cloud providers.
- **Provider terms are respected.** The router is not for account/key rotation to evade limits. It only balances across explicitly configured providers, keys, models, and quotas.
- **LM Studio remains the backstop.** Local OpenAI-compatible endpoints stay usable even when cloud quota is depleted, unhealthy, blocked, or disallowed.

## API surface

OpenAI-compatible:

- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/embeddings`
- `POST /v1/completions`

Operations:

- `GET /health`
- `GET /metrics`
- `GET /dashboard`
- `GET /admin/quota`
- `GET /admin/context`
- `GET /admin/usage`
- `GET /admin/circuits`
- `GET /admin/live-models`
- `POST /admin/live-models/refresh`
- `GET /admin/services`
- `POST /admin/services/scan`
- `GET /admin/outbox`
- `POST /admin/outbox/dispatch`
- `GET /admin/agent-clis`
- `POST /admin/agent-clis/discover`
- `GET /admin/backlog/assistx/config`
- `POST /admin/backlog/dry-run`
- `POST /jobs/agent`

## Logical model aliases

| Alias | Purpose |
|---|---|
| `auto/fast` | Normal interactive fast-free/local routing |
| `auto/flash-start` | Cerebras WSE-3 instant planning/decomposition starter |
| `auto/high-quality` | Local draft plus stronger free refine/judge |
| `auto/code` | Code-focused local draft plus free/cloud refinement |
| `auto/sophia` | Low-latency Sophia realtime profile |
| `auto/backlog-burn` | Controlled surplus-quota burn profile for safe backlog tasks |
| `auto/local` | LM Studio/local-only |
| `auto/private` | LM Studio/local-only with stricter logging/redaction expectations |

## Architecture

```text
Sophia / AssistX / OpenAI-compatible clients / operator dashboard
  -> auto-router FastAPI app
      -> request normalizer
      -> policy engine
      -> AssistX context projection consumer
      -> AssistX read-only backlog task intake
      -> quota manager
      -> provider adapters
      -> service registry and scanner
      -> durable model registry
      -> agent CLI discovery
      -> durable usage ledger
      -> durable event outbox and AssistX dispatcher
  -> local LM Studio endpoints
  -> free hosted provider endpoints
  -> CLI coding agents
  -> AssistX/Neo4j write-back path
```

## Repo layout

```text
.
├── config/                  # provider, policy, agent-worker, and context examples
├── docs/                    # design docs, runbook, deployment, dashboard, discovery notes
├── src/auto_router/         # FastAPI app and routing core
├── tests/                   # unit tests for quota, policy, providers, services, model registry, outbox
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
cp config/context.example.yaml config/context.yaml
make install
make dev
```

Open:

```text
http://localhost:8088/dashboard
```

Or use Docker:

```bash
docker compose up -d --build
```

Point LM Studio/OpenAI-compatible clients at:

```bash
export OPENAI_BASE_URL=http://localhost:8088/v1
export OPENAI_API_KEY=local-router
```

For AssistX alignment, point the context config to the graph-backed projection endpoint:

```bash
export AUTO_ROUTER_CONTEXT_CONFIG=http://assistx:8000/api/router/context-projection
```

For read-only AssistX backlog intake:

```bash
export AUTO_ROUTER_ASSISTX_TASKS_URL=http://assistx:8000/api/router/backlog-candidates
```

## Useful operator commands

```bash
make smoke
curl http://localhost:8088/health | jq
curl http://localhost:8088/v1/models | jq
curl -X POST 'http://localhost:8088/admin/live-models/refresh?provider=cerebras' | jq
curl http://localhost:8088/admin/live-models | jq
curl -X POST http://localhost:8088/admin/services/scan | jq
curl -X POST http://localhost:8088/admin/agent-clis/discover | jq
curl -X POST 'http://localhost:8088/admin/backlog/dry-run?source=assistx&limit=10' -H 'Content-Type: application/json' -d '{"enqueue_events":true}' | jq
curl http://localhost:8088/admin/outbox | jq
curl -X POST 'http://localhost:8088/admin/outbox/dispatch?dry_run=true&limit=10' | jq
```

## Documentation

- [`docs/PRODUCTION_DEPLOYMENT.md`](docs/PRODUCTION_DEPLOYMENT.md) — production topology, persistence, security, deployment, and ops guide
- [`docs/OPERATOR_RUNBOOK.md`](docs/OPERATOR_RUNBOOK.md) — practical run/deploy/smoke-test guide
- [`docs/AGENT_SKILLS.md`](docs/AGENT_SKILLS.md) — Codex/Gemini/OpenCode skill contract and execution safety states
- [`docs/SERVICE_DISCOVERY.md`](docs/SERVICE_DISCOVERY.md) — service registry, service scanning, and durable model registry
- [`docs/HLD.md`](docs/HLD.md) — high-level design
- [`docs/LLD.md`](docs/LLD.md) — low-level design
- [`docs/ROUTER_SWITCHING_MECHANISM.md`](docs/ROUTER_SWITCHING_MECHANISM.md) — detailed switching design for Sophia, backlog, quota, and fallback routing
- [`docs/CEREBRAS_FLASH_NODE.md`](docs/CEREBRAS_FLASH_NODE.md) — Cerebras WSE-3 flash-start lane design
- [`docs/NEO4J_ASSISTX_INTEGRATION.md`](docs/NEO4J_ASSISTX_INTEGRATION.md) — graph projection, Neo4j schema, service registry, agent CLI discovery, and AssistX provenance write-back
- [`docs/DASHBOARD.md`](docs/DASHBOARD.md) — dashboard design
- [`docs/TODO.md`](docs/TODO.md) — prioritized implementation backlog
- [`docs/IDEAS.md`](docs/IDEAS.md) — future router, backlog, Sophia, and graph ideas
- [`docs/QUOTA_STRATEGY.md`](docs/QUOTA_STRATEGY.md) — quota burn-down and reservation model
- [`docs/PROVIDER_MATRIX.md`](docs/PROVIDER_MATRIX.md) — provider notes and volatile limits
- [`docs/SECURITY_PRIVACY.md`](docs/SECURITY_PRIVACY.md) — privacy and key-handling constraints
- [`docs/FLEET_ROUTING_POLICY.md`](docs/FLEET_ROUTING_POLICY.md) — fleet routing policy, privacy classes, and node/model selection guardrails
- [`docs/plans/2026-06-08-xwing-agent-development-handoff.md`](docs/plans/2026-06-08-xwing-agent-development-handoff.md) — verified xwing-first worker readiness and agent kickoff sequence

## Next implementation priorities

1. Add AssistX task claim/approval flow after dry-run selection.
2. Persist remote node CLI/service discovery from AssistX into Neo4j-backed context projection.
3. Add model registry write-back events to AssistX/Neo4j.
4. Add local-vs-cloud dashboard split, circuit retry timers, and backlog queue status.
5. Add background scan/refresh cadence with strict allow-lists and jitter.
