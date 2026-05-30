# Dashboard Design

## 1. Purpose

The dashboard is the operator console for `auto-router`. It should quickly explain what the router is doing, what quota is available to burn, which providers are healthy, whether local fallback is ready, and whether AssistX/Neo4j context agrees with the active routing lanes.

The UI remains intentionally server-rendered with FastAPI, Jinja, Tailwind CDN, and HTMX. No frontend build chain is required.

## 2. Current UI structure

### `/dashboard`

The page now uses a dark operations-console layout with:

1. Hero header for model routing, quota burn, and swarm context.
2. Manual controls for dashboard refresh and Cerebras live-model discovery.
3. Auto-refreshing dashboard summary fragment every 10 seconds.
4. Status cards for providers, free API lane, local backstop, context revision, and Cerebras flash-start activity.
5. Cerebras flash planner card explaining the `auto/flash-start` lane and guardrails.
6. Provider lane cards grouped by context projection status.
7. Quota burn-down cards with progress bars and Cerebras highlighting.
8. Provider health cards and LM Studio loaded-model chips.
9. Agent worker cards.
10. Recent usage table with Cerebras flash-start rows highlighted.

### `/api/dashboard/summary`

HTMX endpoint that renders the live summary fragment.

### `/admin/live-models`

JSON endpoint for cached live provider model inventory.

### `/admin/live-models/refresh`

Refreshes all non-LM Studio provider model inventories.

### `/admin/live-models/refresh?provider=cerebras`

Refreshes Cerebras model inventory only.

## 3. Visual priorities

The dashboard should answer these questions without reading logs:

1. Which routing lanes are ready, blocked, local, or cloud-backed?
2. Is the Cerebras flash-start lane available and being used?
3. How much free quota remains by provider/model/dimension?
4. Is local LM Studio fallback healthy?
5. Are provider circuits open or degraded?
6. What has the router done recently?
7. Is the AssistX/Neo4j context projection fresh and aligned?

## 4. Metrics shown today

- Provider count from quota snapshots.
- Free API lane provider count from context projection.
- Local backstop provider count from context projection.
- Context revision and source.
- Recent Cerebras request count.
- Recent Cerebras average latency when available.
- Quota used/remaining/progress by provider, model, and dimension.
- Provider health and LM Studio loaded model information.
- Recent usage rows with provider, model, route, stage, status, and latency.

## 5. Interaction model

The UI is intentionally low-friction:

- `/dashboard` loads once.
- `#dashboard-content` auto-refreshes every 10 seconds through HTMX.
- The refresh dashboard button manually reloads the summary fragment.
- The Cerebras refresh button posts to `/admin/live-models/refresh?provider=cerebras` and writes the JSON response into the action status area.
- After refreshing live model inventory, the operator can refresh the dashboard fragment to see updated state once the dashboard fragment renders live model tables.

## 6. Known gap

The live-model refresh endpoints are implemented, but the dashboard fragment still needs a dedicated live-model table/card that renders `live_models` once that context is supplied by the live app wrapper. This is the next UI slice.

## 7. Future UI improvements

- Dedicated live model inventory table.
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
