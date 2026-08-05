# Auto-Router High-Level Design

## Document status

- **System:** `auto-router`
- **Purpose:** Canonical high-level design for the strict-offline OpenAI-compatible inference gateway
- **Audience:** AssistX maintainers, router maintainers, fleet operators, runtime owners, and reviewers
- **Authority:** Describes the intended architecture of the current `main` branch. Source code and deployed configuration remain authoritative when they differ.

## 1. Problem statement

Local agent clients need a stable OpenAI-compatible API while the underlying fleet contains multiple model servers, multiple private access paths, different runtime capacities, and changing AssistX-approved runtime projections.

The router must provide protocol compatibility and safe backpressure without becoming a second scheduler, fleet inventory, recovery controller, or durable assignment authority.

## 2. Goals

1. Expose OpenAI-compatible chat, response, embedding, completion, and model-listing endpoints.
2. Operate only against approved local or private-network runtimes.
3. Validate strict-offline configuration before provider state is created.
4. Consume AssistX-approved runtime/model/path projections.
5. Apply bounded admission and priority-aware queueing per physical runtime.
6. Select among approved LAN and Tailscale paths for the same runtime identity.
7. Emit route provenance and operational telemetry back to AssistX.
8. Preserve one shared capacity pool across all paths to one runtime.
9. Fail closed on stale projection, unknown capacity, invalid provider configuration, or unavailable runtime.
10. Reconcile fleet loadout snapshots without corrupting current topology or immutable history.

## 3. Non-goals

The router does not own:

- canonical node, runtime, model, or service inventory;
- task assignment, reservations, claims, leases, or worker placement;
- model loading, unloading, restart, or lifecycle;
- discovery-based admission of new runtimes or paths;
- hosted quota scheduling or cloud fallback;
- durable task state;
- recovery authorization;
- production profile admission.

SQLite and Redis state are ephemeral cache, circuit, queue, idempotency, path-choice, or outbox state only.

## 4. System context

```text
Hermes / OpenCode / local clients
               |
               v
        auto-router gateway
        - request normalization
        - strict-offline validation
        - AssistX projection validation
        - priority-aware admission
        - approved-path selection
        - local forwarding
        - route provenance
               |
               v
   LM Studio / llama.cpp / local runtimes

               ^
               |
       AssistX / Neo4j authority
       - physical identities
       - approved paths
       - loaded-model observations
       - assignments and claims
       - health, capacity, recovery
```

### Related repositories

| Repository | Relationship |
|---|---|
| `auto-assist` | Durable fleet and task authority. Publishes approved runtime projections and consumes route evidence. |
| `lms` | Produces signed physical observation, qualification, canary, and rollback evidence. |
| `fleet-llm-profiles` | Stores desired-state runtime profiles and imported evidence; does not directly control router admission. |

## 5. Architectural principles

### 5.1 Narrow gateway boundary

The router normalizes API requests, applies local policy and admission, chooses among already-approved runtime paths, and forwards requests. Any feature that creates a durable assignment or changes model lifecycle belongs outside the router.

### 5.2 Strict offline by construction

`AUTO_ROUTER_STRICT_OFFLINE=true` is mandatory. Validation occurs before application provider state is built. Public hosts, non-local quota classes, hosted gateways, unsupported provider types, unresolved URLs, and invalid capacity identities cause startup failure.

### 5.3 Physical runtime identity is not an URL

One runtime may be reachable through LAN, Tailscale IP, MagicDNS, LM Studio Link, or a container-local observer path. These paths share one runtime ID and one slot pool. A responding URL cannot independently establish physical ownership.

### 5.4 AssistX projection is authoritative

The runtime projection must be signed or otherwise trusted according to the current contract, monotonic, fresh, and internally consistent. The router may reject or temporarily retain current safe state, but it may not synthesize new authoritative inventory.

### 5.5 Bounded admission

Each physical runtime has explicit parallel slots, queue limit, and queue timeout. Unknown capacity is treated as zero. Queue priority affects only waiting requests; active generations are not preempted.

### 5.6 Fail closed

The router rejects requests when projection state is stale, capacity is unknown, the queue is full, the queue wait times out, executor claims are invalid, no approved access path is reachable, or provider configuration violates strict-offline rules.

## 6. Major subsystems

### 6.1 OpenAI-compatible API

The gateway exposes:

- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/embeddings`
- `POST /v1/completions`

Logical aliases such as `auto/fast`, `auto/high-quality`, `auto/code`, and `auto/local` resolve only to local candidates.

### 6.2 Strict-offline guard

Provider configuration is inspected before provider registry construction. The guard validates host class, provider type, quota class, gateway flags, URLs, runtime identity, capacity, and presence of at least one local runtime.

### 6.3 Runtime projection manager

The projection manager validates a complete AssistX-published generation before atomically replacing current provider, model, path, policy, and admission state. Existing active leases retain their original gate objects until completion; new requests use the new generation.

### 6.4 Admission controller

Admission is keyed by `runtime_instance_id`, not provider alias or URL. It supports:

- explicit parallel capacity;
- bounded wait queue;
- stable priority ordering;
- FIFO ordering within the same priority;
- cancellation-safe and timeout-safe waiter removal;
- direct slot transfer to the next waiter;
- queue and counter telemetry.

### 6.5 Access-path selector

The selector evaluates only AssistX-approved URLs for one runtime. LAN paths are preferred and approved Tailscale paths are fallback. Path health is short-lived and diagnostic. Selection cannot add or approve paths.

### 6.6 Claim fence

For workload routes with AssistX execution semantics, the router verifies the current executor claim before consuming model capacity and revalidates after any queue wait. This prevents an expired or revoked task from using a runtime slot.

### 6.7 Request idempotency and stream lifecycle

Idempotency guards prevent duplicate mutations or duplicated route-side work where applicable. Streaming responses retain the admission lease until the response body completes or is cancelled, then release it in a `finally` path.

### 6.8 Route telemetry and outbox

The router attaches runtime, model, path, projection, slot, and timing metadata to route events. Metadata-only events are dispatched to AssistX through a durable or retryable outbox. Telemetry is observational and cannot alter assignment authority.

### 6.9 Fleet loadout reconciliation

The reconciliation tool probes configured runtimes, distinguishes configured/observed/loaded/routable inventory, and writes:

- mutable current topology records;
- immutable per-snapshot observations;
- loadout assignments and deltas;
- a fenced atomic JSON report.

Neo4j writes occur under a singleton reconciliation lock and monotonic fence version. Empty, non-authoritative, missing-primary, or materially degraded snapshots are rejected unless an explicit destructive override is used.

## 7. Request flow

```text
client request
  -> authenticate/normalize
  -> resolve logical alias and candidate
  -> assert current projection is fresh
  -> validate current AssistX claim when required
  -> acquire priority-aware runtime admission lease
  -> revalidate claim after queue wait
  -> select approved private path
  -> build local provider client
  -> forward request
  -> emit provenance/telemetry
  -> release lease after result or stream completion
```

## 8. Projection update flow

```text
fetch candidate projection
  -> validate identity, signature/checksum, generation, revision, expiry
  -> validate providers, runtimes, models, paths, capacity, aliases
  -> build replacement registry and controllers off-path
  -> atomically publish replacement state
  -> retain prior active gate objects until their leases finish
```

A partial projection must never become visible.

## 9. Loadout reconciliation flow

```text
bounded live probes
  -> classify configured / observed / loaded / routable
  -> validate completeness and degradation thresholds
  -> acquire Neo4j reconciliation lock and fence
  -> write current topology + immutable observations + assignments
  -> commit graph transaction
  -> verify snapshot still owns fence
  -> fsync and atomically replace JSON report
```

A stale reconciler cannot overwrite a newer report.

## 10. Data ownership

| Data | Owner | Router handling |
|---|---|---|
| Task assignment and claim | AssistX/Neo4j | Validate current claim; do not create assignment |
| Runtime/model/path inventory | AssistX plus signed observation evidence | Consume projection; do not invent identity |
| Admission queue and active permits | Router | Ephemeral per-process state |
| Circuit/path latency state | Router | Short-lived diagnostic cache |
| Route provenance | AssistX durable audit, emitted by router | Produce metadata and retry delivery |
| Fleet reconciliation snapshots | Neo4j and fenced report artifact | Router tooling writes under lock/fence |
| Desired node profiles | `fleet-llm-profiles` | External input/evidence only |

## 11. Security boundaries

- Strict-offline validation blocks public inference providers.
- Admin endpoints require configured admin authentication.
- Provider API secrets are obtained from named environment variables and not committed.
- Claim validation prevents unauthorized task use of runtime capacity.
- Approved paths are private LAN, Tailscale, local container, or explicitly accepted internal names.
- Router containers do not mount Hermes worktrees or broad execution state.
- Route telemetry excludes secret values and raw protected content.

## 12. Availability and failure behavior

| Failure | Behavior |
|---|---|
| Projection absent or stale | Reject new routed work; do not synthesize inventory |
| Runtime capacity unknown | Treat as zero and return retryable unavailable response |
| Queue full | Return explicit `429` |
| Queue wait timeout | Return retryable `503` |
| Waiting request cancelled | Remove waiter and do not leak a permit |
| Active stream cancelled | Release permit in stream finalizer |
| Preferred LAN path unavailable | Try next approved path, commonly Tailscale |
| All approved paths unavailable | Fail request and retain diagnostic evidence |
| Claim revoked during queue wait | Reject before provider invocation |
| Discovery partially fails | Reject materially degraded snapshot and preserve last known-good topology |
| Concurrent reconcilers | Serialize through Neo4j lock/fence; stale publisher rejected |

## 13. Deployment model

The normal deployment uses a strict-offline FastAPI process plus Redis where configured. Reconciliation and migration deployments use separate container names, networks, ports, state directories, and provider configuration. Shadow deployments remain isolated from the existing production router until operator-reviewed cutover.

## 14. Architectural decisions

1. AssistX remains the only durable assignment and fleet authority.
2. Router state is ephemeral except for evidence/outbox/reconciliation artifacts with explicit contracts.
3. Runtime identity is independent from path identity.
4. Admission is keyed by physical runtime.
5. Queue priority is non-preemptive and stable.
6. Projection replacement is atomic.
7. Live discovery is observational and cannot load models.
8. Snapshot reconciliation is transactional, fenced, and last-known-good preserving.
9. Hosted fallback is prohibited.

## 15. Related documents

- [`LOW_LEVEL_DESIGN.md`](LOW_LEVEL_DESIGN.md)
- [`fleet_outage_recovery.md`](fleet_outage_recovery.md)
- Repository [`README.md`](../README.md)
- AssistX `docs/HIGH_LEVEL_DESIGN.md` and reconciliation documentation
