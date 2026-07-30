# auto-router

Strict-offline OpenAI-compatible inference gateway for the AssistX fleet.

On branch `full-auto-reconciliation-20260730`, `auto-router` is deliberately narrower than its earlier design. It no longer treats hosted quota, backlog scheduling, worker placement, or broad service discovery as production responsibilities.

## Role

```text
Hermes / OpenCode / local workloads
                |
                v
      auto-router protocol gateway
      - OpenAI-compatible normalization
      - local-provider validation
      - request forwarding/fallback
      - admission/backpressure seam
      - route provenance
                |
                v
         AssistX / Neo4j authority
      - physical node/runtime identity
      - loaded-model observations
      - priority and capability evidence
      - allocation/reservation/claims
      - leases, health, recovery
                |
                v
      LM Studio / LM Studio Link / llama-server
```

AssistX owns decisions. The router forwards requests only to enabled local providers and must not become another scheduler or inventory authority.

## Strict offline mode

`AUTO_ROUTER_STRICT_OFFLINE=true` is mandatory.

Before application state is built, the router validates every enabled provider and fails startup when it finds:

- a public or unresolved provider hostname;
- a non-local quota class;
- a broker/gateway-managed provider;
- an unsupported provider type;
- an invalid provider URL;
- no enabled local runtime.

Allowed hosts include loopback, RFC1918 LAN addresses, Tailscale CGNAT addresses, approved `.ts.net`/`.lan`/`.local`/`.internal` names, and local Docker service names.

Public inference providers—including Groq, Cerebras, OpenRouter, Grok, OpenAI, Anthropic, and hosted gateways—are not part of this deployment.

## What the router owns

- OpenAI-compatible endpoints for chat, responses, embeddings, completions, and model listing.
- Logical aliases such as `auto/fast`, `auto/high-quality`, `auto/code`, and `auto/local`.
- Request normalization and local-provider candidate selection.
- Short-lived circuit, latency, and request-attempt state.
- Route decision/provenance emission back to AssistX.
- A future per-physical-runtime admission semaphore supplied by AssistX capacity state.

## What the router does not own

- canonical node/model/service inventory;
- physical runtime identity;
- task priority or assignment;
- reservations, claims, leases, or heartbeats;
- worker placement or execution;
- model load/unload/restart;
- recovery authority;
- hosted quota scheduling;
- a second durable task or assignment database.

Local SQLite/Redis data is cache, circuit, queue, and outbox state only. Deleting it must not change the canonical next assignment.

## LM Studio Link rule

LM Studio Link may expose a remote loaded model through a local-looking URL. Therefore:

- an access URL is not runtime identity;
- `localhost` may be only the observer/client path;
- `/v1/models` proves API visibility, not physical load ownership;
- physical host/process identity should come from AssistX observations and the official LM Studio CLI (`lms ps --json --host <host>`) when available;
- discovery must never load a model;
- a model already loaded on a linked remote host must not be loaded again because it appeared under localhost.

## Provider configuration

The committed `config/providers.yaml` is local-only. Nodes known to be unstable, offline, or unusably slow are disabled until they pass admission gates.

For a normal local deployment, copy the example and set physical endpoints:

```bash
cp .env.example .env
cp config/providers.example.yaml config/providers.local.yaml
export AUTO_ROUTER_PROVIDER_CONFIG=config/providers.local.yaml
```

Each provider should include a stable physical `node_id`, local `quota_class`, and an access URL that resolves only inside the approved network.

For the live migration shadow deployment, use `config/providers.reconciliation.yaml`. It admits one operator-confirmed physical runtime only. Do not add another node until its physical owner, loaded process, completion health, and slot count are proven.

## Routing policy

All committed profiles use only the `local` provider class. Legacy aliases are retained for client compatibility but map to local behavior:

- `auto/flash-start` -> local planner;
- `auto/backlog-burn` -> local batch;
- `auto/high-quality` -> local draft/refine/judge;
- `auto/code` -> local code stages.

No alias permits hosted fallback.

## API surface

OpenAI-compatible:

- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/embeddings`
- `POST /v1/completions`

AssistX integration:

- `POST /api/routes/request`
- `POST /api/memory/events`
- `POST /api/memory/context`
- metadata-only event outbox dispatch to AssistX

Operations endpoints remain available for local diagnostics, but their snapshots are not canonical when they differ from AssistX/Neo4j.

## Normal local start

```bash
cp .env.example .env
make install
make dev
```

Or:

```bash
docker compose up -d --build
```

The default Compose deployment is strict-offline, has autoload/unload and the in-process fleet dispatcher disabled, and does not mount Hermes or worktree execution state into the router.

## Side-by-side migration start

When the old router is still running, do **not** run the base Compose file alone. The reconciliation overlay creates separate container names, Redis/data, Docker network, and loopback port `18088`:

```bash
mkdir -p data-reconciliation artifacts-reconciliation

docker compose \
  -f docker-compose.yml \
  -f compose.reconciliation.yml \
  config > artifacts-reconciliation/router-rendered.yaml

docker compose \
  -f docker-compose.yml \
  -f compose.reconciliation.yml \
  up -d --build redis llm-router

curl -fsS http://127.0.0.1:18088/health | jq
curl -fsS http://127.0.0.1:18088/v1/models | jq
```

The shadow router expects the reconciliation AssistX API on `127.0.0.1:18000` through `host.docker.internal`. Follow the complete runbook in `auto-assist/docs/LOCAL_AGENT_LIVE_MIGRATION_RUNBOOK_20260730.md` on the matching branch.

Point shadow clients at:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:18088/v1
export OPENAI_API_KEY=local-offline-only
```

AssistX must use the router-only overlay. `auto-assign` is retired and must not run in the reconciled deployment.

## Verification

```bash
pytest -q tests/test_offline_guard.py
pytest -q
curl http://127.0.0.1:8088/health | jq
curl http://127.0.0.1:8088/v1/models | jq
```

A deployment is not admitted merely because `/v1/models` succeeds. Each physical model instance also needs a completion canary, fresh health/capacity observation, and benchmark evidence before AssistX routes real work.

## Next required implementation

1. Add the physical runtime/model-instance observation contract shared with AssistX.
2. Ingest LM Studio `ps --host` observations without confusing Link access paths with runtime owners.
3. Add per-runtime slot admission and queue limits; unknown capacity defaults to zero.
4. Remove or disable legacy hosted-provider discovery/admin paths.
5. Remove scheduler/backlog semantics that overlap AssistX.
6. Make all workload routes consume an AssistX reservation or signed route decision.

The authoritative cross-repository decision and live migration package are in `auto-assist` on the matching branch.
