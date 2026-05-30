# Service Discovery and Model Registry

## 1. Purpose

`auto-router` now separates three related but different concerns:

1. **Service registry**: known URLs and ownership from AssistX/Neo4j or YAML bootstrap.
2. **Service scanning**: opt-in local/private probes that update status and latency.
3. **Model registry**: durable inventory of provider-hosted models discovered through OpenAI-compatible `/models` endpoints.

This separation keeps the router safe and predictable. Registration tells the UI what exists. Scanning tells the UI what is reachable right now. Model discovery tells the router/operator what hosted providers currently expose.

## 2. Service registry sources

Services may come from:

- `config/context.yaml` during bootstrap;
- AssistX graph-backed projection at `/api/router/context-projection`;
- future node agents that self-report local services;
- future Neo4j `Service` nodes.

Service records can be attached at three levels:

```yaml
services: []          # global services
nodes[].services: []  # node-owned services
providers[].services: [] # provider-owned services
```

## 3. Service scanning

The scanner probes `health_url` when present, otherwise `url`.

Supported probes:

| Scheme | Behavior |
|---|---|
| `http://` | GET request, online when status < 500 |
| `https://` | GET request, external hosts skipped unless allowed |
| `bolt://` | TCP connection probe |
| `redis://` | TCP connection probe |
| `tcp://` | TCP connection probe |

Local/private probing is allowed by default for:

- localhost;
- loopback IPs;
- private IP ranges;
- link-local IPs;
- single-label hostnames such as `deathstar-XPS-8920`;
- `.lan` hostnames.

External providers such as Cerebras/Groq/OpenRouter are skipped unless the operator explicitly uses:

```bash
curl -X POST 'http://localhost:8088/admin/services/scan?allow_external=true' | jq
```

## 4. Service scan persistence

Every scan result is written to SQLite table `service_scan_events`.

Latest status is hydrated on startup and merged back into the in-memory context so the dashboard remains useful after restarts.

Scan results are also queued as outbox events:

```text
router.service_snapshot.recorded
```

These events can later be dispatched to AssistX and merged into Neo4j service history.

## 5. Model registry

Live hosted model inventory can change. The router preserves discovered inventory in SQLite table `model_registry_snapshots`.

Refresh providers:

```bash
curl -X POST http://localhost:8088/admin/live-models/refresh | jq
curl -X POST 'http://localhost:8088/admin/live-models/refresh?provider=cerebras' | jq
```

Inspect latest cached/registered models:

```bash
curl http://localhost:8088/admin/live-models | jq
```

The endpoint returns:

- current in-memory cache;
- durable registry summary;
- recent registry snapshots.

On startup, `auto-router` hydrates the in-memory live-model cache from the latest durable registry snapshots.

## 6. Why model registry matters

The model registry supports production hardening by making hosted model drift visible:

- provider adds/removes models;
- API key becomes invalid;
- `/models` endpoint changes format;
- free-tier models change availability;
- dashboard can show last known model inventory even during provider outage;
- AssistX can later store model availability history in Neo4j.

## 7. Suggested Neo4j model registry shape

```text
(RouterProvider {provider_id})-[:PUBLISHED_MODEL]->(RouterModel {model_id})
(RouterModel)-[:DISCOVERED_IN]->(ModelRegistrySnapshot {fetched_at, ok, error, model_count})
(RouterContextProjection)-[:INCLUDES_MODEL_SNAPSHOT]->(ModelRegistrySnapshot)
```

Useful properties:

| Node | Properties |
|---|---|
| `RouterModel` | `model_id`, `provider`, `owned_by`, `capabilities`, `first_seen_at`, `last_seen_at` |
| `ModelRegistrySnapshot` | `provider`, `ok`, `fetched_at`, `expires_at`, `model_count`, `error` |

## 8. Production defaults

Recommended production posture:

- Context projection comes from AssistX over private network.
- Service scanning remains local/private unless operator-triggered with external probing.
- Model refresh is operator-triggered or scheduled at low frequency.
- Event dispatch remains operator-triggered until AssistX event sink is stable.
- Prompt logging stays disabled.
- Route execution events exclude prompt and response bodies.

## 9. Future improvements

- Background service scan cadence with allow-list and jitter.
- Background model refresh cadence for selected volatile providers.
- Dashboard live-model inventory table.
- AssistX write-back for model registry snapshots.
- Neo4j drift reports: provider changed models, service disappeared, CLI became unavailable.
- Node self-registration agent for remote Tailscale machines.
