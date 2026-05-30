# Operator Runbook

## 1. Purpose

This runbook is the practical checklist for running `auto-router` as the local routing, quota, service-launchpad, and agent-capability discovery node.

Use the enhanced app wrapper by default:

```bash
uvicorn auto_router.main_live:app --host 0.0.0.0 --port 8088
```

`auto_router.main_live` includes the base OpenAI-compatible router plus live model discovery, service registry/scan routes, event outbox routes, AssistX event dispatch, and agent CLI discovery routes.

## 2. First boot

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

## 3. Docker boot

```bash
docker compose up --build
```

Health check:

```bash
curl http://localhost:8088/health
```

OpenAI-compatible client configuration:

```bash
export OPENAI_BASE_URL=http://localhost:8088/v1
export OPENAI_API_KEY=local-router
```

## 4. Smoke checks

Run locally:

```bash
make smoke
```

Manual API checks:

```bash
curl http://localhost:8088/v1/models | jq
curl http://localhost:8088/admin/context | jq
curl http://localhost:8088/admin/services | jq
curl http://localhost:8088/admin/outbox | jq
curl http://localhost:8088/admin/agent-clis | jq
```

## 5. Cerebras flash-start setup

1. Add `CEREBRAS_API_KEY` to `.env`.
2. Ensure `config/providers.yaml` includes the `cerebras` provider.
3. Ensure `config/policies.yaml` maps `auto/flash-start` to `flash_start_planner`.
4. Refresh live models:

```bash
curl -X POST 'http://localhost:8088/admin/live-models/refresh?provider=cerebras' | jq
```

5. Send a flash-start request:

```bash
curl http://localhost:8088/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "auto/flash-start",
    "messages": [{"role":"user","content":"Create a concise implementation plan for the router backlog."}],
    "max_completion_tokens": 700,
    "metadata": {"allow_cloud": true, "source": "operator"}
  }' | jq
```

## 6. Service launchpad and scanning

The dashboard renders service URLs from `config/context.yaml` or the AssistX context projection.

Scan local/private services only:

```bash
curl -X POST http://localhost:8088/admin/services/scan | jq
```

External probes are disabled by default. To explicitly scan hosted APIs:

```bash
curl -X POST 'http://localhost:8088/admin/services/scan?allow_external=true' | jq
```

Scan results are persisted to SQLite and queued as `router.service_snapshot.recorded` events in the outbox.

## 7. Agent CLI discovery

Discover host-local agent CLIs:

```bash
curl -X POST http://localhost:8088/admin/agent-clis/discover | jq
```

The router checks for:

- `codex`
- `gemini`
- `opencode`

Discovery is capability reporting only. Scheduling is still policy-gated by credits, subscription reset, safety rules, and write/commit approval.

Discovery events are queued as `router.agent_cli.discovered` events in the outbox.

## 8. Outbox workflow

Inspect pending events:

```bash
curl http://localhost:8088/admin/outbox | jq
```

Dry-run dispatch without changing event state:

```bash
curl -X POST 'http://localhost:8088/admin/outbox/dispatch?dry_run=true&limit=10' | jq
```

Dispatch pending events to AssistX when `AUTO_ROUTER_ASSISTX_EVENT_SINK_URL` is configured:

```bash
curl -X POST 'http://localhost:8088/admin/outbox/dispatch?limit=25' | jq
```

Mark delivered manually after external processing:

```bash
curl -X POST http://localhost:8088/admin/outbox/<event_id>/delivered | jq
```

Mark retry/dead-letter manually:

```bash
curl -X POST 'http://localhost:8088/admin/outbox/<event_id>/failed?error=manual-test&retry=true' | jq
curl -X POST 'http://localhost:8088/admin/outbox/<event_id>/failed?error=terminal&retry=false' | jq
```

## 9. AssistX context projection and event sink

When AssistX exposes the graph-backed projection endpoint, set:

```bash
AUTO_ROUTER_CONTEXT_CONFIG=http://assistx:8000/api/router/context-projection
```

When AssistX exposes an idempotent event sink, set:

```bash
AUTO_ROUTER_ASSISTX_EVENT_SINK_URL=http://assistx:8000/api/events
AUTO_ROUTER_ASSISTX_EVENT_DISPATCH_TIMEOUT_SECONDS=10
AUTO_ROUTER_ASSISTX_EVENT_DISPATCH_MAX_ATTEMPTS=5
```

The projection should include nodes, providers, services, and eventually agent CLI capabilities. The event sink should accept service snapshots, agent CLI discovery events, and later route execution events.

## 10. Safety rules

- Keep `AUTO_ROUTER_LOG_PROMPTS=false` unless debugging non-sensitive local-only data.
- Treat CLI discovery as a local-only/node-agent function.
- Service scanning should remain private-network scoped unless explicitly allowed.
- Keep event dispatch operator-triggered until the AssistX event sink is stable.
- Do not enable agent write/commit/push by default.
- Cloud routing must honor `local_only`, `allow_cloud=false`, voice-auth, enrollment, secrets, and private-memory labels.
