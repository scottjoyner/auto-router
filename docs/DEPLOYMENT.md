# Deployment Guide

This guide covers the aligned deployment where AssistX is already running and `auto-router` is introduced as a separate OpenAI-compatible service.

## Deployment model

- AssistX remains the source of truth for Neo4j context, task authority, and the live context projection.
- `auto-router` runs as its own service on port `8088`.
- `auto-router` reads `AUTO_ROUTER_CONTEXT_CONFIG` from AssistX `/api/context/projection` when the live projection is available.
- The router keeps its own Redis-backed quota state and local SQLite usage data.
- Clients point to the router, not AssistX, for OpenAI-compatible LLM traffic.

## Recommended topology

### Option A: shared Docker network

Use a shared Docker network if both services run on the same host and are containerized.

- AssistX container is reachable as `http://assistx-api:8000` or similar service DNS.
- Router uses:

```bash
AUTO_ROUTER_CONTEXT_CONFIG=http://assistx-api:8000/api/context/projection
```

This is the cleanest setup because the router never depends on host-level DNS tricks.

### Option B: host-reachable AssistX service

Use host reachability if AssistX already runs as a host service or you want the fastest path to first deployment.

- AssistX is reachable on `http://host.docker.internal:8000` on Docker Desktop.
- On Linux, add `extra_hosts: ["host.docker.internal:host-gateway"]` to the router service.
- Router uses:

```bash
AUTO_ROUTER_CONTEXT_CONFIG=http://host.docker.internal:8000/api/context/projection
```

This works well for a first cut, but keep the projection endpoint on a private network boundary.

## AssistX requirements

AssistX must already be reachable before the router starts if you want live context sync at boot.

Required facts from AssistX:

- Neo4j is running and populated with node and model inventory.
- `GET /api/context/projection` returns a JSON snapshot.
- The projection includes node/provider lane facts and any free API credit hints the graph knows about.

If AssistX is temporarily unavailable, `auto-router` falls back to its local bootstrap context config and still starts.

## Router environment

At minimum, set:

```bash
AUTO_ROUTER_HOST=0.0.0.0
AUTO_ROUTER_PORT=8088
AUTO_ROUTER_PROVIDER_CONFIG=/app/config/providers.example.yaml
AUTO_ROUTER_POLICY_CONFIG=/app/config/policies.example.yaml
AUTO_ROUTER_AGENT_CONFIG=/app/config/agent_workers.example.yaml
AUTO_ROUTER_CONTEXT_CONFIG=http://assistx-api:8000/api/context/projection
AUTO_ROUTER_REDIS_URL=redis://redis:6379/0
AUTO_ROUTER_DATABASE_URL=sqlite:////data/router.sqlite3
```

Provider API keys remain provider-specific environment variables, for example:

- `GEMINI_API_KEY`
- `GROQ_API_KEY`
- `CEREBRAS_API_KEY`
- `MISTRAL_API_KEY`
- `OPENROUTER_API_KEY`
- `GITHUB_TOKEN`
- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `ZAI_API_KEY`

For local LM Studio backends, keep the host URLs in `config/providers.yaml` or override them with environment variables.

## AssistX environment

AssistX stays on its existing deployment stack. The only deployment requirement introduced by the router alignment is that AssistX must expose the projection endpoint internally.

Important AssistX settings:

- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE=assistx`
- `REDIS_URL`
- `BASIC_AUTH_USER`
- `BASIC_AUTH_PASS`
- `PAPERCLIP_*` secrets

## Example router compose fragment

```yaml
services:
  llm-router:
    build: .
    ports:
      - "8088:8088"
    environment:
      AUTO_ROUTER_HOST: 0.0.0.0
      AUTO_ROUTER_PORT: 8088
      AUTO_ROUTER_PROVIDER_CONFIG: /app/config/providers.example.yaml
      AUTO_ROUTER_POLICY_CONFIG: /app/config/policies.example.yaml
      AUTO_ROUTER_AGENT_CONFIG: /app/config/agent_workers.example.yaml
      AUTO_ROUTER_CONTEXT_CONFIG: http://assistx-api:8000/api/context/projection
      AUTO_ROUTER_REDIS_URL: redis://redis:6379/0
      AUTO_ROUTER_DATABASE_URL: sqlite:////data/router.sqlite3
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./config:/app/config:ro
      - ./data:/data
    depends_on:
      - redis
    restart: unless-stopped
```

If you use a shared network with AssistX, replace the `AUTO_ROUTER_CONTEXT_CONFIG` URL with the AssistX service DNS name instead of `host.docker.internal`.

## Startup order

1. Start AssistX and verify `/api/context/projection` returns JSON.
2. Start Redis for the router.
3. Start `auto-router`.
4. Check `GET /health` on the router.
5. Check `GET /dashboard` for context providers, local providers, and free API providers.
6. Point OpenAI-compatible clients at `http://<router-host>:8088/v1`.

## Validation checklist

- AssistX `/api/context/projection` responds successfully.
- Router `/health` shows a non-bootstrap `context_revision`.
- Router `/health` lists the expected local providers and free API providers.
- Router `/v1/models` exposes lane metadata.
- Streaming and non-streaming chat requests succeed through the router.
- Local-only requests stay on local providers.

## Failure modes

- If AssistX is down, the router should still start and use bootstrap context.
- If the projection is stale, the router continues with the last fetch and bootstrap fallback.
- If provider keys are missing, health checks should show the affected provider as unhealthy without taking the router down.
- If Redis is unavailable, the router should fall back to in-memory quota state only for local development; production should not rely on that fallback.

## Deployment order summary

- AssistX first.
- Router second.
- Clients last.

## Related docs

- [ALIGNMENT_EVENT.md](ALIGNMENT_EVENT.md)
- [HLD.md](HLD.md)
- [LLD.md](LLD.md)
