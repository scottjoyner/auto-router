# auto-router

Local-first OpenAI-compatible LLM router for aggressively using legitimate free cloud LLM quota on high-priority deliverables, then falling back to local LM Studio endpoints across the homelab.

## Goal

`auto-router` exposes a local endpoint that looks like a standard LM Studio / OpenAI-compatible server while routing each request through a policy engine:

1. classify task priority, privacy, modality, and quality needs;
2. draft with cheap/local models when useful;
3. refine, judge, or repair using the best available free cloud quota for high-priority deliverables;
4. consume daily/monthly free quota intentionally before reset;
5. fail back to LM Studio endpoints when free cloud quota is exhausted, unhealthy, or disallowed by privacy policy.

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

## Design principles

- **Free quota is a resource to schedule.** Daily quotas should be spent on useful work before reset, not accidentally left idle.
- **Quality beats raw cost on high-priority deliverables.** Important work can use a local draft plus a stronger free-tier refinement/judge pass.
- **Local-first privacy.** Requests tagged `local_only`, likely sensitive, or matching configured privacy rules bypass cloud providers.
- **Provider terms are respected.** The router is not for account/key rotation to evade limits. It only balances across explicitly configured providers, keys, models, and quotas.
- **LM Studio remains the backstop.** Local OpenAI-compatible endpoints such as `http://r2d2:1234/v1` or `http://deathstar-XPS-8920:1234/v1` should be usable even when every cloud quota is depleted.
- **Dashboard-first operations.** The operator should see remaining quota, burn-down, fallbacks, latency, errors, and which high-priority deliverables received refine/judge passes.

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

See [`docs/PROVIDER_MATRIX.md`](docs/PROVIDER_MATRIX.md) and [`config/providers.example.yaml`](config/providers.example.yaml).

## Planned architecture

```text
OpenAI-compatible clients
  -> auto-router FastAPI container
      -> privacy classifier
      -> task/priority classifier
      -> quota-aware policy engine
      -> provider adapters
      -> Redis quota reservations
      -> SQLite/Postgres usage ledger
      -> dashboard + metrics
  -> free cloud endpoints
  -> LM Studio fallback endpoints
```

## Repo layout

```text
.
├── config/                  # provider and policy examples
├── docs/                    # HLD, LLD, quota strategy, dashboard, roadmap
├── src/auto_router/         # FastAPI skeleton and routing core
├── tests/                   # unit tests for quota and policy primitives
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

docker compose up --build
```

Then point LM Studio/OpenAI-compatible clients at:

```bash
export OPENAI_BASE_URL=http://localhost:8088/v1
export OPENAI_API_KEY=local-router
```

## Documentation

- [`docs/HLD.md`](docs/HLD.md) — high-level design
- [`docs/LLD.md`](docs/LLD.md) — low-level design
- [`docs/QUOTA_STRATEGY.md`](docs/QUOTA_STRATEGY.md) — quota burn-down and reservation model
- [`docs/PROVIDER_MATRIX.md`](docs/PROVIDER_MATRIX.md) — provider notes and volatile limits
- [`docs/DASHBOARD.md`](docs/DASHBOARD.md) — dashboard design
- [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) — execution plan
- [`docs/SECURITY_PRIVACY.md`](docs/SECURITY_PRIVACY.md) — privacy and key-handling constraints
