# Production Deployment Guide

## 1. Target role

`auto-router` should run as the routing control plane for the homelab/AssistX stack. In production, it should:

- expose a local OpenAI-compatible API;
- route requests across local LM Studio, free hosted providers, and future agent workers;
- keep Redis-backed quota reservation state;
- persist usage, service scans, model registry snapshots, and event outbox data in SQLite;
- render the operator dashboard;
- consume AssistX/Neo4j context projection;
- dispatch durable outbox events to AssistX when the sink is available.

## 2. Recommended topology

```text
clients / Sophia / AssistX / LM-compatible apps
        ↓
private network / Tailscale / LAN
        ↓
auto-router :8088
        ↓
Redis :6379
SQLite volume ./data/router.sqlite3
        ↓
local LM Studio endpoints
hosted free provider APIs
AssistX context projection + event sink
Neo4j behind AssistX
```

Keep `auto-router` private-network only unless you add authentication and TLS in front of it.

## 3. Deployment modes

### 3.1 Docker Compose

Recommended first production mode:

```bash
cp .env.example .env
cp config/providers.example.yaml config/providers.yaml
cp config/policies.example.yaml config/policies.yaml
cp config/agent_workers.example.yaml config/agent_workers.yaml
cp config/context.example.yaml config/context.yaml
docker compose up --build -d
```

Verify:

```bash
curl http://localhost:8088/health | jq
curl http://localhost:8088/v1/models | jq
curl http://localhost:8088/admin/services | jq
```

### 3.2 Systemd / bare metal

Useful if the router needs direct access to host-local agent CLIs such as `codex`, `gemini`, or `opencode`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
make smoke
uvicorn auto_router.main_live:app --host 0.0.0.0 --port 8088
```

For systemd, set the working directory to the repo root, load `.env`, and run:

```text
ExecStart=/path/to/repo/.venv/bin/uvicorn auto_router.main_live:app --host 0.0.0.0 --port 8088
```

## 4. Persistent data

Persist these paths:

| Path | Purpose |
|---|---|
| `./data/router.sqlite3` | usage ledger, service scan history, model registry, event outbox |
| `redis-data` Docker volume | Redis quota reservations and transient counters |
| `./config` | provider/policy/agent/context configs |
| `./artifacts` | future agent artifacts |
| `./worktrees` | future agent worktrees |

Back up SQLite before major upgrades:

```bash
sqlite3 ./data/router.sqlite3 '.backup ./data/router-$(date +%Y%m%d-%H%M%S).sqlite3'
```

## 5. Required environment

Minimum:

```bash
AUTO_ROUTER_CONTEXT_CONFIG=config/context.yaml
AUTO_ROUTER_REDIS_URL=redis://localhost:6379/0
AUTO_ROUTER_DATABASE_URL=sqlite:///./data/router.sqlite3
AUTO_ROUTER_LOG_PROMPTS=false
```

AssistX integration:

```bash
AUTO_ROUTER_CONTEXT_CONFIG=http://assistx:8000/api/router/context-projection
AUTO_ROUTER_ASSISTX_EVENT_SINK_URL=http://assistx:8000/api/events
```

Provider keys as needed:

```bash
CEREBRAS_API_KEY=...
GROQ_API_KEY=...
GEMINI_API_KEY=...
OPENROUTER_API_KEY=...
```

## 6. Reverse proxy and network security

Recommended:

- Bind `auto-router` to LAN/Tailscale only.
- Put Caddy/Nginx/Traefik in front only if you add auth and TLS.
- Keep `/admin/*` private.
- Do not expose `/admin/services/scan` publicly.
- Do not expose `/admin/agent-clis/discover` publicly.
- Do not expose `/admin/outbox/dispatch` publicly.

Example Caddy private-network stance:

```text
router.example.internal {
  reverse_proxy 127.0.0.1:8088
}
```

Add authentication before exposing outside private network.

## 7. Startup validation checklist

Run after deployment:

```bash
curl http://localhost:8088/health | jq
curl http://localhost:8088/v1/models | jq
curl http://localhost:8088/admin/context | jq
curl http://localhost:8088/admin/services | jq
curl http://localhost:8088/admin/live-models | jq
curl http://localhost:8088/admin/outbox | jq
```

Then run controlled operations:

```bash
curl -X POST http://localhost:8088/admin/services/scan | jq
curl -X POST http://localhost:8088/admin/agent-clis/discover | jq
curl -X POST 'http://localhost:8088/admin/live-models/refresh?provider=cerebras' | jq
curl -X POST 'http://localhost:8088/admin/outbox/dispatch?dry_run=true&limit=10' | jq
```

## 8. Operational procedures

### 8.1 Service discovery

```bash
curl -X POST http://localhost:8088/admin/services/scan | jq
```

Only local/private services are probed by default.

### 8.2 Hosted model registry refresh

```bash
curl -X POST http://localhost:8088/admin/live-models/refresh | jq
```

For one provider:

```bash
curl -X POST 'http://localhost:8088/admin/live-models/refresh?provider=cerebras' | jq
```

### 8.3 CLI capability discovery

```bash
curl -X POST http://localhost:8088/admin/agent-clis/discover | jq
```

This is especially useful on hosts with Codex, Gemini CLI, or OpenCode installed.

### 8.4 Outbox dispatch

Dry-run:

```bash
curl -X POST 'http://localhost:8088/admin/outbox/dispatch?dry_run=true&limit=10' | jq
```

Real dispatch:

```bash
curl -X POST 'http://localhost:8088/admin/outbox/dispatch?limit=25' | jq
```

## 9. Health and metrics

Health:

```bash
curl http://localhost:8088/health | jq
```

Prometheus-style metrics:

```bash
curl http://localhost:8088/metrics
```

Current metrics include quota remaining, request count, and circuit state. Future metrics should add service status, model registry status, outbox backlog, and local-vs-cloud split.

## 10. Upgrade checklist

Before pull/deploy:

```bash
sqlite3 ./data/router.sqlite3 '.backup ./data/router-preupgrade.sqlite3'
git pull
make smoke
```

After deploy:

```bash
curl http://localhost:8088/health | jq
curl http://localhost:8088/admin/outbox | jq
curl http://localhost:8088/admin/live-models | jq
```

## 11. Failure handling

| Failure | Action |
|---|---|
| Provider key missing | Provider health/model refresh will report error; configure key or disable provider |
| Redis unavailable | Quota manager may fall back depending on implementation; restore Redis quickly |
| SQLite locked | Stop duplicate router instance or inspect long-running writes |
| AssistX context unreachable | Router uses existing/bootstrap context; check context source in `/health` |
| AssistX event sink unreachable | Events remain pending/retry in outbox |
| Service scan reports offline | Check node/Tailscale/DNS/health URL before changing policy |
| Agent CLI missing | Install CLI on that node or have another node self-report capability |

## 12. Production safety defaults

- Use `auto_router.main_live:app` in production.
- Keep prompt logging disabled.
- Keep admin endpoints private.
- Do not schedule agent write/commit/push by default.
- Keep service scanning local/private unless explicitly testing hosted APIs.
- Dispatch outbox manually until AssistX event sink is mature.
- Treat discovery data as advisory; policy still decides whether something may run.
