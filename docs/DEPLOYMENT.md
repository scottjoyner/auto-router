# Deployment Guide

This guide covers the aligned deployment where AssistX is already running and `auto-router` is introduced as a separate OpenAI-compatible service.

## Deployment model

- AssistX remains the source of truth for Neo4j-backed context objects, task authority, and the live graph projection.
- `auto-router` runs as its own service on port `8088`.
- `auto-router` reads `AUTO_ROUTER_CONTEXT_CONFIG` from AssistX `/api/context/projection` when the live projection is available.
- The router keeps its own Redis-backed quota state and local SQLite usage data.
- Clients point to the router, not AssistX, for OpenAI-compatible LLM traffic.
- Deployment verification should confirm canonical provider/model IDs are flowing through `/v1/models`, the dashboard, and the ops summaries.

## Network and ingress policy

Internal service-to-service traffic should use private Docker networks. Remote operator/client access should use an explicitly managed ingress layer rather than publishing every application port on `0.0.0.0`.

For tailnet access, follow [TAILSCALE_DOCKER.md](TAILSCALE_DOCKER.md). The preferred model is a stable per-service Tailscale identity or an intentional central Caddy ingress; do not accidentally mix both for the same hostname. Tailscale sidecars must persist their state and deployment agents must validate application health separately from tailnet health.

Before an autonomous agent modifies Docker, Caddy, Tailscale, or service exposure, it must satisfy [AGENT_INFRA_VISIBILITY.md](AGENT_INFRA_VISIBILITY.md). If the host reports active storage/filesystem I/O failures, freeze deployment reconciliation until the host is stable.

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
- `GET /api/context/projection` returns a JSON snapshot of graph objects, not a markdown export.
- The projection includes node/provider lane facts, live endpoint facts, and any free API credit hints the graph knows about.

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

The host-port mapping above is intentionally a minimal example. For production tailnet access, prefer the Tailscale sidecar or central ingress patterns in `TAILSCALE_DOCKER.md` and remove unnecessary LAN-wide port publication.

If you use a shared network with AssistX, replace the `AUTO_ROUTER_CONTEXT_CONFIG` URL with the AssistX service DNS name instead of `host.docker.internal`.

## Startup order

1. Pass the host-health gate in `AGENT_INFRA_VISIBILITY.md`.
2. Start AssistX and verify `/api/context/projection` returns JSON.
3. Start Redis for the router.
4. Start Tailscale sidecar/ingress if that service uses one, and verify it owns a tailnet IP.
5. Start `auto-router`.
6. Check `GET /health` on the router from inside its application namespace.
7. Check `GET /dashboard` for context providers, local providers, and free API providers.
8. Validate the HTTPS/MagicDNS endpoint from an independent tailnet node.
9. Point OpenAI-compatible clients at the validated `/v1` endpoint.

## Validation checklist

- Host has no active storage/filesystem failure blocking safe deployment.
- AssistX `/api/context/projection` responds successfully.
- Router `/health` shows a non-bootstrap `context_revision`.
- Router `/health` lists the expected local providers and free API providers.
- Router `/v1/models` exposes lane metadata.
- Streaming and non-streaming chat requests succeed through the router.
- Local-only requests stay on local providers.
- If a Tailscale sidecar is used, it has a persistent state directory and stable tailnet hostname.
- Tailnet DNS resolves from an independent node.
- HTTPS reaches the intended backend, with the expected redirect/TLS behavior.
- No application port is published to the LAN unless there is a documented reason.

## Failure modes

- If AssistX is down, the router should still start and use bootstrap context.
- If the projection is stale, the router continues with the last fetch and bootstrap fallback.
- If provider keys are missing, health checks should show the affected provider as unhealthy without taking the router down.
- If Redis is unavailable, the router should fall back to in-memory quota state only for local development; production should not rely on that fallback.
- If the Tailscale sidecar is green but application health fails, debug the application namespace; do not assume ingress is healthy.
- If the application is green but tailnet HTTPS fails, inspect Tailscale identity, Serve config, DNS/ACL policy, and ingress independently.
- If Caddy currently owns ingress, do not stop it merely to test Tailscale Serve.
- If the host reports storage I/O errors or filesystem corruption, stop deployment changes and switch to recovery workflow.

## Startup order summary

1. Run the full test suite locally: `pytest -q`.
2. Pass the host-health and visibility checks.
3. Start AssistX first and verify `/api/context/projection` returns JSON.
4. Start Redis for the router.
5. Start the intended ingress/tailnet sidecar without replacing unrelated ingress.
6. Start `auto-router` with Docker Compose (`docker compose up -d --build`) or the local app wrapper.
7. Check `GET /health` on the router.
8. Check `GET /admin/ops/summary`, `GET /dashboard`, and `GET /v1/models` for canonical provider/model identity.
9. Validate DNS + HTTPS from a second tailnet node.
10. Point OpenAI-compatible clients at the validated `/v1` endpoint.

## Related docs

- [TAILSCALE_DOCKER.md](TAILSCALE_DOCKER.md)
- [AGENT_INFRA_VISIBILITY.md](AGENT_INFRA_VISIBILITY.md)
- [ALIGNMENT_EVENT.md](ALIGNMENT_EVENT.md)
- [HLD.md](HLD.md)
- [LLD.md](LLD.md)
