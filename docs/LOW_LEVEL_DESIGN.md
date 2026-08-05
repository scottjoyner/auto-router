# Auto-Router Low-Level Design

## Document status

- **System:** `auto-router`
- **Scope:** Current process structure, provider validation, runtime projection, admission, forwarding, telemetry, and fleet reconciliation
- **Companion:** [`HIGH_LEVEL_DESIGN.md`](HIGH_LEVEL_DESIGN.md)
- **Implementation authority:** Source code, Pydantic models, committed workflow tests, and deployed configuration override this document when they differ.

## 1. Source layout

The primary implementation lives under `src/auto_router/`.

| Area | Primary modules |
|---|---|
| Base OpenAI-compatible application | `main.py`, request/response models in `models.py` |
| Reconciled strict-offline entrypoint | `main_live.py`, `secure_live.py` |
| Provider creation and forwarding | provider modules and `providers.py` |
| Strict-offline validation | `offline_guard.py` |
| Runtime projection | `runtime_projection.py`, `runtime_projection_v2.py` |
| Admission | `admission.py` |
| Private access paths | `access_paths.py` |
| AssistX claim validation | `claim_fence.py` |
| Idempotency | `request_idempotency.py` |
| Streaming lifecycle | `stream_lifecycle.py` |
| Route events and provenance | `route_events.py`, `route_event_patch.py` |
| AssistX-only route mounting | `strict_assistx_routes.py`, AssistX route modules |
| Fleet dispatch compatibility | `fleet_task_dispatcher.py`, not a durable assignment authority |
| Fleet loadout reconciliation | `scripts/build_fleet_loadouts.py`, `scripts/discover_fleet_models.py` |

The reconciled process starts through `auto_router.main_live:app`, which validates offline configuration before importing and constructing the base application provider state.

## 2. Process initialization

### 2.1 Import-time guard

`main_live.py` performs this sequence:

1. import only the strict-offline validation module;
2. require `AUTO_ROUTER_STRICT_OFFLINE` to remain enabled;
3. validate the configured provider file and environment expansion;
4. only then import the base application and provider-building modules;
5. replace dispatch functions with reconciled admission/path/claim wrappers;
6. remove retired inherited routes;
7. register only the intended operator and AssistX integration surfaces;
8. attach the strict-offline lifespan.

This ordering prevents invalid hosted or ambiguous provider state from being constructed before validation.

### 2.2 Lifespan startup

The strict-offline lifespan:

- loads base router state;
- builds one `RuntimeAdmissionController` over enabled providers;
- builds one `RuntimeAccessPathSelector`;
- creates the `RuntimeProjectionManager`;
- starts local housekeeping and outbox tasks;
- starts projection polling only when a projection URL is configured;
- cancels all background tasks on shutdown;
- persists bounded latency state best-effort without changing assignment authority.

## 3. Configuration models

### 3.1 Provider configuration

Representative provider fields include:

```text
name
type
node_id
runtime_instance_id
runtime_kind
runtime_version
headless
parallel_slots
queue_limit
queue_timeout_seconds
base_url
access_urls[]
api_key_env
priority
quota_class
models[]
gateway_managed
local_gateway_only
```

### 3.2 Model configuration

Representative model fields include:

```text
alias
provider_model
model_instance_id
artifact_fingerprint
quantization
capabilities[]
context_window
quota metadata
```

### 3.3 Request priority

The current request priority enum is ordered for admission purposes as:

```text
critical
repo_critical
interactive
local_only
batch
background
```

The exact enum declaration in `models.py` is authoritative. Priority affects queued requests only and never interrupts an active generation.

## 4. Strict-offline validation

The guard evaluates each enabled provider before application state construction.

### 4.1 Required conditions

- strict-offline mode is enabled;
- provider type is allowlisted for local/private operation;
- quota class is local;
- gateway/broker flags do not route to a hosted intermediary;
- `base_url` and every `access_url` parse correctly;
- host resolves to loopback, private LAN, Tailscale CGNAT, approved internal DNS, or explicit local container service;
- positive capacity has a stable `runtime_instance_id`;
- model aliases and provider identities are not conflicting;
- at least one enabled local runtime is present unless a specific zero-capacity bootstrap configuration is used by the reconciled projection flow.

### 4.2 Rejection examples

Startup fails for:

- OpenAI, OpenRouter, Anthropic, Groq, Cerebras, or other public inference endpoints;
- unresolved or public DNS names;
- malformed provider URLs;
- duplicated aliases with incompatible identities;
- environment placeholders that did not expand;
- unsupported provider types;
- positive slots attached only to an endpoint alias without physical runtime identity.

## 5. Runtime projection manager

### 5.1 Projection responsibilities

A projection represents one complete AssistX-approved generation of:

- providers and physical runtime IDs;
- loaded model instances and aliases;
- ordered access paths;
- capacity, queue, and policy settings;
- generation/revision identity;
- checksum or signature metadata;
- issuance and expiry.

### 5.2 Apply sequence

```text
receive projection payload
  -> parse and validate schema
  -> verify generation/revision monotonicity
  -> verify checksum/signature contract
  -> verify expiry and freshness
  -> validate all provider/model/path/capacity identities
  -> construct candidate provider registry
  -> construct candidate admission controller
  -> construct candidate access-path selector
  -> atomically swap complete state
```

A failed candidate leaves the active generation unchanged and records a bounded error for operator diagnostics.

### 5.3 Generation replacement

Active requests retain references to the provider/gate objects they acquired. New requests use the replacement generation. This prevents an atomic projection update from invalidating an in-flight lease while still preventing new work from entering a revoked runtime.

## 6. Runtime admission controller

### 6.1 Gate identity

One `_RuntimeGate` exists per `runtime_instance_id`. Multiple providers with the same runtime ID must provide identical admission configuration or startup fails. Provider names map to runtime keys; missing runtime identity maps to an unresolved zero-capacity gate.

### 6.2 Gate state

Each gate stores:

```text
config
async lock
active count
queued waiter list
monotonic waiter sequence
acquired_total
rejected_total
timed_out_total
cancelled_total
```

A waiter contains priority, insertion sequence, and a future that receives the lease.

### 6.3 Acquire algorithm

1. Reject zero or unknown capacity with retryable `503`.
2. Lock the gate.
3. If a slot is free and no waiters exist, increment `active` and return a lease.
4. If queue is disabled or full, increment rejection counter and return `429`.
5. Create a waiter with priority and sequence; append it.
6. Release lock and wait with timeout using a shielded future.
7. On timeout, reacquire lock, remove the waiter if not already granted, increment timeout count, and return retryable `503`.
8. On cancellation, remove the waiter or release a lease that raced with cancellation, increment cancellation count, and re-raise.

### 6.4 Release algorithm

1. Lock the gate.
2. Ignore duplicate release when `active <= 0` through the lease's idempotent release guard.
3. Decrement `active`.
4. Select the live waiter with minimum `(priority_rank, sequence)`.
5. Remove it from the queue.
6. Increment `active` and acquired count before completing its future, directly transferring the slot.

Direct transfer prevents a later request from bypassing the existing queue between release and wakeup.

### 6.5 Snapshot

`GET /admin/admission` includes, per runtime:

- runtime ID;
- slots and queue configuration;
- active and queued counts;
- queued count by priority;
- acquired, rejected, timed-out, and cancelled totals.

These counters are process-local operational state.

## 7. Claim validation

For routes backed by AssistX work:

1. Assert current projection freshness before queue entry.
2. Validate the request's task/executor claim against AssistX or the current trusted claim projection.
3. Acquire runtime admission.
4. Revalidate the claim after any queue wait.
5. Only then invoke the local provider.

This closes the race in which a task could be revoked while waiting for capacity.

## 8. Access-path selection

### 8.1 Candidate set

Only configured or projected approved paths for the selected runtime are considered. The selector does not scan the network or create new paths.

### 8.2 Ordering

Paths preserve AssistX-approved order, normally:

1. same-LAN RFC1918 URL;
2. Tailscale `100.64.0.0/10` URL;
3. approved MagicDNS or `.ts.net` URL;
4. other explicitly approved internal path.

### 8.3 Selection behavior

The selector applies a bounded probe timeout and short cache TTL. It returns an `AccessPathChoice` containing runtime ID, selected URL, transport classification, and diagnostic metadata. Failure of all paths produces a provider error and no inference call.

### 8.4 Identity invariant

Changing the selected URL does not change:

- physical runtime ID;
- loaded model instance ID;
- capacity slot pool;
- reservation or claim identity.

## 9. Provider dispatch

### 9.1 Non-streaming

The reconciled dispatch wrapper:

```text
assert projection fresh
  -> acquire admission using request priority
  -> revalidate executor claim
  -> select approved access path
  -> clone provider config with selected URL
  -> build bounded provider client
  -> annotate route telemetry
  -> call base dispatch
  -> release lease in finally
```

### 9.2 Streaming

Streaming follows the same pre-invocation path. If provider setup or initial dispatch fails, the lease is released immediately. On success, the response body is wrapped in an async iterator that releases the lease in `finally` after stream completion, error, or cancellation.

### 9.3 Provider client construction

The selected URL is applied to a copy of the provider configuration. Secrets remain referenced by environment variable. Client timeouts come from router settings. Provider construction must not mutate the canonical projection object.

## 10. Request idempotency

Idempotency storage is local and bounded. It may retain request identity, in-progress/completed markers, and response metadata sufficient to prevent unsafe duplicate route-side effects. It is not a task database and must not be used to recreate AssistX assignments after deletion.

Idempotency keys should bind at least request identity, route, caller scope, and relevant body fingerprint. Streaming and failed requests must transition to terminal or retryable states without leaving permanent false in-progress records.

## 11. Route telemetry

Before forwarding, the router attaches non-secret facts such as:

```text
runtime projection generation/revision/checksum/expiry
node_id
runtime_instance_id
runtime kind/version/headless state
selected transport and URL
parallel slots and queue settings
model_instance_id
logical alias and provider model
artifact fingerprint and quantization
context length
```

Completion telemetry adds attempt outcome, latency, time-to-first-token, token counts, error classification, and path/circuit observations where available. Events use correlation and request IDs and are sent to AssistX through the route event/outbox path.

Raw API keys and secret headers must never be included.

## 12. Mounted route surface

The reconciled entrypoint intentionally mounts a reduced surface:

- OpenAI-compatible inference endpoints;
- health/model visibility needed by local clients;
- AssistX integration routes;
- memory/context event routes where explicitly retained;
- fleet/operator diagnostics;
- authenticated projection and admission administration.

Legacy routes that imply in-process agent scheduling, hosted provider control, autonomous model loading, or retired `auto-assign` behavior are not mounted.

## 13. Fleet discovery

### 13.1 Probe sources

The discovery tool probes configured LM Studio nodes using bounded per-node budgets and supported endpoints such as:

- `/api/v1/models`
- `/api/v0/models`
- `/v1/models`

Native runtime APIs provide stronger loaded-process authority than compatibility visibility. Cached reports and dispatcher statistics are diagnostic only.

### 13.2 Inventory classes

Discovery records separate sets for:

- configured models;
- observed API-visible models;
- authoritatively loaded models;
- routable models.

Each observation includes endpoint status, source, warnings, timestamp, authority, and completeness.

### 13.3 No lifecycle mutation

Discovery cannot load, unload, restart, or move a model. A model seen through LM Studio Link or localhost compatibility does not prove local physical ownership.

## 14. Fleet loadout reconciliation

### 14.1 Graph records

The builder maintains:

- mutable current node/model state;
- immutable node/model observation records per snapshot;
- fleet snapshot identity and timestamps;
- task profiles and loadouts;
- primary/reviewer/fallback assignments;
- deltas from prior accepted snapshot;
- singleton reconciliation lock and fence version.

### 14.2 Transaction algorithm

1. Complete live discovery and local validation outside the write transaction.
2. Reject no-authority, empty, missing-primary, or materially degraded candidates unless explicit override is present.
3. Open one managed Neo4j write transaction.
4. Ensure required uniqueness constraints exist.
5. Acquire/update the singleton reconciliation lock and monotonic fence.
6. Resolve previous/current snapshot only after the lock is owned.
7. Write snapshot, immutable observations, mutable current state, loadouts, assignments, and deltas.
8. Commit transaction.
9. Before report publication, verify the committed snapshot still owns the lock/fence.
10. Write report to a temporary file, flush and fsync, then atomically replace the final JSON path.

### 14.3 Degradation policy

Default policy rejects a drop greater than the configured fraction in authoritative nodes or routable models. Explicit `--allow-degraded-snapshot` or `--allow-empty-snapshot` flags are destructive operator overrides and must be visible in the report.

### 14.4 Concurrency behavior

Concurrent first-run reconcilers serialize through the singleton lock. Each accepted run receives an ordered fence. A slow earlier process cannot publish after a later accepted process owns the fence.

## 15. Persistence boundaries

| Store | Allowed data | Prohibited authority |
|---|---|---|
| In-memory admission/path state | Active permits, waiters, probes, counters | Durable assignment |
| Redis | Queue/cache/outbox/idempotency as configured | Canonical task or runtime identity |
| SQLite/local files | Latency, circuit, route cache, reports | Fleet authority |
| Neo4j reconciliation graph | Current topology, immutable observations, loadouts, fences | Worker execution authority beyond AssistX contract |
| AssistX Neo4j | Tasks, assignments, claims, health, approvals | Managed externally; router consumes/validates |

## 16. Error mapping

| Condition | HTTP/error behavior |
|---|---|
| Invalid/offline provider configuration | Startup failure |
| Projection stale or invalid | Conflict/unavailable; new work blocked |
| Zero/unknown runtime capacity | Retryable `503` |
| Queue full | `429` |
| Queue timeout | Retryable `503` |
| Invalid or revoked executor claim | Authorization/conflict response before inference |
| No approved path healthy | Retryable provider unavailable |
| Provider timeout | Bounded provider error with route attempt evidence |
| Client cancellation | Cancel provider/stream where supported and release lease |
| Reconciliation candidate destructive | Nonzero command failure; last-known-good state preserved |
| Stale report publisher | Publication rejected |

## 17. Configuration and secrets

Important settings include:

```text
AUTO_ROUTER_STRICT_OFFLINE
AUTO_ROUTER_PROVIDER_CONFIG
AUTO_ROUTER_ADMIN_TOKEN or configured admin auth
AUTO_ROUTER_RUNTIME_PROJECTION_URL
AUTO_ROUTER_ACCESS_PATH_TTL_SECONDS
AUTO_ROUTER_ACCESS_PATH_PROBE_TIMEOUT_SECONDS
AUTO_ROUTER_MAX_FLEET_DROP_FRACTION
NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD / NEO4J_DATABASE
provider-specific API key environment names
```

Production credentials must come from secret files or environment injection and must not be committed. Reconciliation requires explicit Neo4j credentials and permission to create required constraints.

## 18. Observability

Operator-visible signals include:

- health and current projection status;
- runtime admission snapshots;
- queued requests by priority;
- access-path selection and recent probe state;
- route attempts, latency, TTFT, token counts, and errors;
- outbox delivery state;
- fleet discovery completeness and authority;
- reconciliation snapshot/fence/report identity.

Metrics and diagnostics remain projections, not assignment authority.

## 19. Test strategy

### Unit tests

- strict-offline host/provider validation;
- access path ordering and fallback;
- admission capacity, queue bounds, priority, FIFO, timeout, and cancellation;
- claim revalidation;
- request idempotency;
- projection parsing, monotonicity, and atomic replacement;
- stream lease release;
- route telemetry redaction;
- discovery parsing and authority classification;
- loadout validation and degradation rejection.

### Integration tests

- strict-offline router plus Redis container startup;
- health, authentication, model listing, inference, route surface, and admin gates;
- ephemeral Neo4j sequential snapshots;
- concurrent first-run reconciliation with constraints removed;
- stale publisher rejection.

### Physical gates

CI cannot prove real LAN/Tailscale movement, LM Studio Link ownership, runtime process cleanup, thermal behavior, physical capacity, or production Neo4j permissions. These require separate operator evidence.

## 20. Change rules

Changes that add provider classes, alter physical identity, change projection semantics, modify admission ordering, mount legacy routes, or weaken snapshot fencing require updates to this LLD and the HLD plus dedicated regression coverage. No router change may create an independent durable assignment or model lifecycle authority.
