# auto-router

Strict-offline OpenAI-compatible inference gateway for the AssistX fleet.

On branch `full-auto-reconciliation-20260730`, `auto-router` is deliberately narrower than its earlier design. It no longer treats hosted quota, backlog scheduling, worker placement, model placement, or broad service discovery as production responsibilities.

## Role

```text
Hermes / OpenCode / local workloads
                |
                v
      auto-router protocol gateway
      - OpenAI-compatible normalization
      - local-provider validation
      - LAN-first / Tailscale path selection
      - per-runtime admission and backpressure
      - request forwarding
      - route provenance
                |
                v
         AssistX / Neo4j authority
      - physical node/runtime identity
      - approved runtime access paths
      - loaded-model observations
      - priority and capability evidence
      - allocation/reservation/claims
      - leases, health, recovery
                |
                v
      LM Studio / LM Studio Link / llama-server
```

AssistX owns decisions. The router forwards requests only to enabled local runtimes and must not become another scheduler or inventory authority.

## Strict offline mode

`AUTO_ROUTER_STRICT_OFFLINE=true` is mandatory.

Before application state is built, the router validates every enabled provider and fails startup when it finds:

- a public or unresolved provider hostname in `base_url` or `access_urls`;
- a non-local quota class;
- a broker/gateway-managed provider;
- an unsupported provider type;
- an invalid provider URL;
- positive slot capacity without a physical `runtime_instance_id`;
- no enabled local runtime.

Allowed hosts include loopback, RFC1918 LAN addresses, Tailscale CGNAT addresses, approved `.ts.net`/`.lan`/`.local`/`.internal` names, and local Docker service names.

Public inference providers—including Groq, Cerebras, OpenRouter, Grok, OpenAI, Anthropic, and hosted gateways—are not part of this deployment.

## What the router owns

- OpenAI-compatible endpoints for chat, responses, embeddings, completions, and model listing.
- Logical aliases such as `auto/fast`, `auto/high-quality`, `auto/code`, and `auto/local`.
- Request normalization and local-provider candidate selection.
- A bounded admission gate per physical `runtime_instance_id`.
- Queue limits, queue timeout, cancellation-safe permit release, and explicit 429/503 responses.
- Selection among AssistX-approved access paths for the same runtime.
- Short-lived circuit, latency, path-choice, and request-attempt state.
- Route decision/provenance emission back to AssistX.

## What the router does not own

- canonical node/model/service inventory;
- physical runtime identity;
- discovery or approval of new access paths;
- task priority or assignment;
- reservations, claims, leases, or heartbeats;
- worker placement or execution;
- model load/unload/restart;
- recovery authority;
- hosted quota scheduling;
- a second durable task or assignment database.

Local SQLite/Redis data is cache, circuit, queue, path-choice, and outbox state only. Deleting it must not change the canonical next assignment.

## LAN and Tailscale access-path rule

One physical runtime may have several private access paths, for example:

```yaml
runtime_instance_id: lmstudio-xwing-1234
parallel_slots: 1
access_urls:
  - http://192.168.1.51:1234/v1
  - http://100.90.80.70:1234/v1
  - http://xwing.example.ts.net:1234/v1
```

The paths are ordered. The router probes the same-LAN URL first and falls back to the approved Tailscale IP or MagicDNS URL when the node is away from the local network. All paths retain one runtime identity, one model-process identity, and one shared slot pool.

Path selection is not discovery. AssistX must first create or approve the candidate paths from host-side inventory and Tailscale observations.

The reconciliation container remains on a normal Docker bridge network so host-published APIs can stay on `127.0.0.1`. Cutover validation must prove from inside the router container that:

- the preferred RFC1918 endpoint is reachable while the node is local;
- the Tailscale `100.64.0.0/10` endpoint is reachable when the LAN path is unavailable;
- the selected path shown by `GET /admin/admission` matches the expected transport.

Use the discovered Tailscale IP when Docker does not inherit host split-DNS behavior. MagicDNS names remain supported when they resolve inside the container.

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

Each admitted provider should include:

- a stable physical `node_id`;
- a stable `runtime_instance_id`;
- explicit `parallel_slots`;
- a bounded `queue_limit` and `queue_timeout_seconds`;
- ordered private `access_urls`, with LAN before Tailscale;
- local `quota_class`;
- one or more model aliases.

For the live migration shadow deployment, use `config/providers.reconciliation.yaml`. It admits one operator-confirmed physical runtime only. Do not add another node until its physical owner, loaded process, completion health, container network reachability, and slot count are proven.

## Fleet loadout reconciliation safety

The fleet loadout builder reads live runtime inventory and writes both a current
routing view and immutable snapshot observations. Production reconciliation
requires explicit Neo4j credentials:

```bash
export NEO4J_URI=bolt://127.0.0.1:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD='set-in-a-secret-store'
export NEO4J_DATABASE=neo4j
python scripts/build_fleet_loadouts.py
```

A normal run fails closed when discovery contains no authoritative loaded model,
no task profiles, or a loadout without a primary assignment. It also serializes
reconcilers and rejects routable-model or authoritative-node drops greater than
50% by default, preserving the last known-good topology during partial discovery
outages. Tune the threshold with `AUTO_ROUTER_MAX_FLEET_DROP_FRACTION` or
`--max-fleet-drop-fraction`. `--allow-degraded-snapshot` and
`--allow-empty-snapshot` are explicit destructive overrides for intentional
fleet changes or drains. Reconciliation creates Neo4j uniqueness constraints
for every mutable and immutable state identity, including the singleton writer
lock. Reports are published atomically under the same lock/version fence only
after the Neo4j transaction commits, so rejected, partial, concurrent, or stale
reconciliation attempts cannot replace the last committed report or expose a
partially built graph topology. The Neo4j account must have permission to create
constraints; reconciliation fails closed when those invariants cannot be enforced.
The dedicated Neo4j contract drops the constraints, starts two first-run
reconcilers simultaneously, verifies one ordered fence chain and one current
snapshot, and proves an older publisher cannot overwrite the winning JSON report.

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

Operator diagnostics:

- `GET /admin/admission` — authenticated ephemeral slot, queue, approved-path, and selected-path state.

Operations snapshots are not canonical when they differ from AssistX/Neo4j.

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

When the old router is still running, do **not** run the base Compose file alone. The reconciliation overlay creates separate container names, Redis/data, Docker networks, and loopback port `18088`:

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
curl -fsS -H "X-Admin-Token: $AUTO_ROUTER_ADMIN_TOKEN" \
  http://127.0.0.1:18088/admin/admission | jq
```

The router and shadow AssistX API communicate over the dedicated `assistx_reconciliation_shared` Docker network. Their host-published ports remain bound to `127.0.0.1`.

Point shadow clients at:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:18088/v1
export OPENAI_API_KEY=local-offline-only
```

AssistX must use the router-only overlay. `auto-assign` is retired and must not run in the reconciled deployment.

## Verification

```bash
pytest -q tests/test_access_paths.py
pytest -q tests/test_admission.py
pytest -q tests/test_offline_guard.py
pytest -q
curl http://127.0.0.1:8088/health | jq
curl http://127.0.0.1:8088/v1/models | jq
```

A deployment is not admitted merely because `/v1/models` succeeds. Each physical model instance also needs a completion canary, fresh health/capacity observation, explicit container reachability evidence, and benchmark evidence before AssistX routes real work.

## Remaining integration work

1. Ingest approved multi-path runtime observations directly from the AssistX context projection rather than only the reconciliation provider file.
2. Require an AssistX reservation or signed route decision for workload routes that need durable assignment semantics.
3. Continue removing legacy hosted-provider discovery/admin code that is no longer mounted by `main_live`.
4. Promote live LAN/Tailscale failover evidence into the migration ledger before cutover.

The authoritative cross-repository decision and live migration package are in `auto-assist` on the matching branch.
