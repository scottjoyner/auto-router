# Dashboard Design

## 1. Purpose

The dashboard is the operator console for `auto-router`. It should quickly explain what the router is doing, what quota is available to burn, which providers are healthy, whether local fallback is ready, which services are available, and whether AssistX/Neo4j context agrees with the active routing lanes.

The UI remains intentionally server-rendered with FastAPI, Jinja, Tailwind CDN, and HTMX. No frontend build chain is required.

## 2. Current UI structure

### `/dashboard`

The page now uses a dark operations-console layout with:

1. Hero header for model routing, quota burn, service links, and swarm context.
2. Manual controls for dashboard refresh, Cerebras live-model discovery, and local/private service scanning.
3. Auto-refreshing dashboard summary fragment every 10 seconds.
4. Status cards for providers, services, free API lane, local backstop, context revision, and Cerebras flash-start activity.
5. Service launchpad that renders clickable URLs from the AssistX/Neo4j context projection.
6. Cerebras flash planner card explaining the `auto/flash-start` lane and guardrails.
7. Provider lane cards grouped by context projection status, with provider-specific service links.
8. Quota burn-down cards with progress bars and Cerebras highlighting.
9. Node cards with node-specific service links.
10. Recent usage table with Cerebras flash-start rows highlighted.

### `/api/dashboard/summary`

HTMX endpoint that renders the live summary fragment.

### `/admin/live-models`

JSON endpoint for cached live provider model inventory.

### `/admin/live-models/refresh`

Refreshes all non-LM Studio provider model inventories.

### `/admin/live-models/refresh?provider=cerebras`

Refreshes Cerebras model inventory only.

### `/admin/services`

JSON endpoint for registered service URLs, merged with the latest in-memory service scan results.

### `/admin/services/scan`

Scans registered service health URLs with external probing disabled by default. This probes local/private/LAN/Tailscale-style targets and skips hosted external APIs unless `allow_external=true` is explicitly provided.

## 3. Visual priorities

The dashboard should answer these questions without reading logs:

1. Which routing lanes are ready, blocked, local, or cloud-backed?
2. Which service URLs can I jump to from here?
3. Is the Cerebras flash-start lane available and being used?
4. How much free quota remains by provider/model/dimension?
5. Is local LM Studio fallback healthy?
6. Are provider circuits open or degraded?
7. What has the router done recently?
8. Is the AssistX/Neo4j context projection fresh and aligned?

## 4. Metrics shown today

- Provider count from quota snapshots.
- Registered service count from context projection.
- Free API lane provider count from context projection.
- Local backstop provider count from context projection.
- Context revision and source.
- Recent Cerebras request count.
- Recent Cerebras average latency when available.
- Quota used/remaining/progress by provider, model, and dimension.
- Provider health and LM Studio loaded model information.
- Node and provider service links.
- Recent usage rows with provider, model, route, stage, status, and latency.

## 5. Interaction model

The UI is intentionally low-friction:

- `/dashboard` loads once.
- `#dashboard-content` auto-refreshes every 10 seconds through HTMX.
- The refresh dashboard button manually reloads the summary fragment.
- The Cerebras refresh button posts to `/admin/live-models/refresh?provider=cerebras` and writes the JSON response into the action status area.
- The service scan button posts to `/admin/services/scan` and writes scan results into the action status area.
- Service scans are local/private by default; external hosted probes require explicit `allow_external=true`.

## 6. Service registry model

Services come from the context projection and may be registered at three levels:

- top-level `services` for global URLs such as auto-router, AssistX, Neo4j, and Redis;
- `node.services` for node-local URLs such as LM Studio or Neo4j Browser on a specific machine;
- `provider.services` for provider-owned URLs such as Cerebras, Groq, or OpenRouter API endpoints.

The dashboard deduplicates by `service_id` through `context.all_services()` and renders links by `priority`.

## 7. Known gaps

- Scan status is currently in-memory; AssistX/Neo4j write-back should persist service health snapshots later.
- The live-model refresh endpoints are implemented, but the dashboard fragment still needs a dedicated live-model table/card that renders `live_models` once that context is supplied by the live app wrapper.
- The service scanner does not yet run on a background cadence.

## 8. Future UI improvements

- Dedicated live model inventory table.
- Persisted service health history from AssistX/Neo4j.
- Burn-down sparkline or line chart.
- Local-vs-cloud request split.
- Open circuit cards with retry-after timers.
- Backlog queue and burn scheduler status.
- Provider-specific detail drawers.
- Manual provider disable/enable.
- Manual quota override.
- LM Studio endpoint benchmark table.
- OpenCode provider config export.
- Queue view for background work waiting for surplus quota.
