# Swarm Unification Execution Plan

This document turns the swarm-unification checklist into an execution-ready plan.

It is meant to answer three questions:
1. What is already working?
2. What is missing or only partially implemented?
3. What should be patched next, in what order, and why?

Scope:
- auto-assign
- auto-router
- paperclip
- AssistX / Neo4j

Important note:
- In this local filesystem snapshot, only `~/git/auto-router` is present and inspectable.
- The other repos were not present for direct source inspection here, so their status is inferred from prior session context and design docs.
- Treat the inferred sections as a plan baseline, not a verified runtime audit.

Related doc:
- `docs/SWARM_UNIFICATION_BACKLOG.md` for the file-by-file patch plan and command list.

---

## 1. Current state matrix

| Repo | What is up | What is partial | What is missing / needs verification |
|---|---|---|---|
| auto-assign | Conceptual role is clear: task placement, scoring, scheduling, ownership | Docs/contracts appear to exist from prior context | No locally verified deployment, scheduler tick, or event write-back proof |
| auto-router | Live OpenAI-compatible router, dashboard, ops, live model registry, service registry, CLI discovery, outbox, route signals | Provider/model swarm unification, context projection, agentgateway sidecar, and signal hydration are partly in place | Need end-to-end validation of canonical IDs, privacy hard stops, failover, and AssistX write-back |
| paperclip | Execution/workforce plane is defined in the system design | Prior context indicates a running worker/orchestrator surface on at least one host | No local source inspection; heartbeat/claim/complete schema not verified here |
| AssistX / Neo4j | Graph memory / canonical state is the intended source of truth | Router integration docs and projection contract exist | No live verification of graph ingestion, docs sync, or provenance replay in this snapshot |

---

## 2. Repo-by-repo evaluation

### 2.1 auto-assign

Role:
- task intake normalization
- assignment scoring
- queue and ownership selection
- policy-aware placement
- emits assignment / heartbeat / completion events

What should already exist:
- stable event schema for assignments and worker lifecycle
- readiness/backlog classification
- capability-aware scoring
- read-only router hints
- write-back to AssistX / Neo4j
- docs for swarm-element mapping

What appears to be in good shape:
- the conceptual boundary is already correct: auto-assign should be the placement brain, not the router
- integration contracts and event contracts were referenced in earlier work

Gaps:
- no live repo inspection in this snapshot
- no proof that assignment events are being produced or ingested
- no proof that scheduler/daemon startup is healthy
- no verified worker-heartbeat loop or stale-lease handling

Patch priorities:
1. Add or verify canonical task / assignment / heartbeat event schemas.
2. Add a scheduler health endpoint and tick path that can be smoke-tested.
3. Add a read-only router capability query contract.
4. Add idempotent graph write-back for assignment state.
5. Add tests for privacy gating, stale leases, and explainable scoring.

Acceptance criteria:
- a task can be assigned, re-queried, and tracked without ambiguity
- assignments are visible in AssistX / Neo4j
- auto-assign does not duplicate provider/routing logic

---

### 2.2 auto-router

Role:
- execution router and control plane
- provider/model selection
- privacy, quota, circuit breaker, and fallback enforcement
- live model and provider registry projection
- route signal emission and outbox write-back
- dashboard and operator surface

What is already up in this snapshot:
- OpenAI-compatible API surface exists
- dashboard and ops surfaces exist
- live model discovery and provider registry persistence exist
- service discovery/scanning exists
- CLI discovery exists
- backlog dry-run and outbox dispatch exist
- route signal / swarm summary work is already present in code and docs
- agentgateway sidecar integration exists in docs and code path coverage

What is partial:
- canonical provider/model swarm unification is in progress
- route signals are visible in summary paths, but end-to-end hydration needs confirmation
- agentgateway is conceptually integrated, but real-world smoke proof is still needed
- privacy / local-only guarantees need one final pass across every route path

Gaps:
- ensure provider aliases collapse to canonical provider-scoped model IDs
- ensure every `/models`-capable provider is projected into the same live inventory used by dashboard and routing
- ensure route signals are emitted on both success and failure, then hydrated back into context immediately
- ensure AssistX write-back payloads remain metadata-only
- ensure agentgateway sidecar fallback is validated with real traffic

Patch priorities:
1. Finalize canonical identity rules for providers, models, and nodes.
2. Make the live inventory and context projection use the same normalized swarm snapshot.
3. Verify route signal emission + hydration on both success and failure.
4. Verify local-only / privacy enforcement across chat/responses/embeddings/completions.
5. Verify agentgateway sidecar mode with smoke traffic and fallback rules.
6. Keep dashboard/ops summaries aligned with the same counts the router uses for policy.

Acceptance criteria:
- provider count, model count, and signal counts agree across API, dashboard, and ops
- canonical IDs are stable and deduplicated
- no private/local-only request can leak to cloud-backed routes
- route provenance stays metadata-only

---

### 2.3 paperclip

Role:
- worker execution plane
- claim/check-out/check-in loop
- heartbeat and load reporting
- task execution and progress updates
- workforce management

What should be up:
- worker identity and registration
- heartbeat endpoint
- claim/release semantics
- progress and completion events
- mapping from assignment IDs to worker runs
- sandbox / allow-listing if code execution is supported

What appears to be true from prior context:
- paperclip exists as part of the broader orchestration system
- the system design expects it to be the workforce layer, not the router

Gaps:
- no local checkout or runtime validation in this snapshot
- no verified heartbeat schema or claim path
- no proof that worker state is mirrored into the graph consistently

Patch priorities:
1. Add or verify worker heartbeat and load schema.
2. Add claim/release lifecycle endpoints or scripts.
3. Add task progress and completion events.
4. Ensure worker state can be projected into AssistX / Neo4j.
5. Add tests for stale workers, duplicate claims, and task completion idempotency.

Acceptance criteria:
- a worker can heartbeat, claim, progress, and complete a task
- the assignment and worker run can be joined in the graph
- paperclip does not own routing logic

---

### 2.4 AssistX / auto-assist / Neo4j

Role:
- canonical graph state for tasks, assignments, routes, providers, models, services, and workers
- task lifecycle authority and approval boundary
- live context projection source for auto-router
- provenance and audit trail
- documentation and knowledge sync
- approved execution handoff to Paperclip / Hermes where required

What is already up:
- router context projection from AssistX-style data is documented and implemented on the router side
- route provenance outbox exists on the router side
- AssistX exposes read-only router endpoints for context projection and backlog dry-run candidates
- Paperclip/Hermes are the approved execution path during the current cutover
- graph-backed coordination is part of the system vision and prior session work

What is partial:
- the router can consume projection data, but live AssistX projection must be verified at runtime and must not silently degrade to bootstrap without a warning
- graph write-back from auto-router into AssistX / Neo4j needs end-to-end confirmation
- documentation sync into Neo4j is part of the plan, but not verified here

Gaps:
- no live validation of the AssistX context projection from the router runtime in this snapshot
- no live validation that route events are posted back to AssistX / Neo4j through the event sink
- no live docs sync verification in this snapshot
- no single authoritative schema for task/assignment/route/worker/provider/model nodes has been fully validated here

Patch priorities:
1. Define the canonical graph nodes and relationships for swarm elements across auto-router, auto-assist, and Neo4j.
2. Make AssistX context projection the primary live source of truth and treat bootstrap only as a fallback/degraded state.
3. Ensure route events, assignment events, and worker events are stored as metadata-only provenance.
4. Ensure AssistX event sink, backlog candidates, and task lifecycle endpoints remain aligned with router expectations.
5. Ensure docs sync covers all target repos.
6. Ensure graph queries can answer “why did this run here?”, “what is blocked?”, and “which execution path is approved?”

Acceptance criteria:
- Neo4j holds the current swarm state
- auto-router, auto-assist, and Paperclip/Hermes agree on task/context provenance boundaries
- route and assignment provenance can be queried without prompt/response bodies
- docs are searchable and cross-linked

---

## 3. Cross-repo patching plan

This is the order I would patch the system in.

### P0 - Normalize the contract layer

Goal:
Make sure every repo speaks the same language for tasks, assignments, workers, providers, models, and routes.

Patch set:
- Canonical IDs for provider, model, node, worker, task, assignment, and request
- Metadata-only event envelope for route / assignment / heartbeat / completion events
- Privacy and local-only field names and hard-stop semantics
- Deduplication rules for aliases vs canonical records

Deliverables:
- one shared event contract doc
- one shared identity mapping doc
- one shared provenance policy doc

### P1 - Finish auto-router swarm unification

Goal:
Make the router the authoritative execution inventory for providers and live models.

Patch set:
- unify provider and model inventory projection
- keep dashboard and ops summaries tied to the same snapshot as routing
- ensure route signals are emitted and hydrated back into context immediately
- verify agentgateway sidecar behavior

Deliverables:
- canonical provider/model snapshot
- route-signal projection docs
- sidecar smoke test results

### P2 - Connect auto-assign to router capabilities

Goal:
auto-assign should place tasks using capability summaries from the router, without knowing transport details.

Patch set:
- add read-only capability query to router if missing
- add assignment decision payloads that reference capabilities, not endpoints
- add idempotent assignment write-back into AssistX / Neo4j
- add stale lease and retry logic

Deliverables:
- assignment schema and decision doc
- scheduler health + tick docs
- tests proving no provider logic leaks into auto-assign

### P3 - Make paperclip execution-visible

Goal:
Make task claiming and worker heartbeats visible as graph/state events.

Patch set:
- heartbeat and claim lifecycle schema
- progress and completion event schema
- graph projection for worker runs
- sandbox / allow-list policy if code execution is supported

Deliverables:
- worker lifecycle doc
- task claim / completion tests
- graph joinability of assignment -> worker run

### P4 - Close the AssistX / Neo4j loop

Goal:
Every important state change should be visible in the graph without leaking prompt bodies.

Patch set:
- graph schema for task, assignment, worker, provider, model, route decision, and service snapshot
- provenance outbox replay / idempotency
- docs sync pipeline verification
- operator query recipes

Deliverables:
- graph schema doc
- replay policy doc
- docs sync verification report

---

## 4. What to patch first in auto-router

If you want the highest-leverage immediate patch sequence, do this first:

1. Canonicalize provider/model IDs everywhere.
2. Verify the live inventory and context projection are the same object graph.
3. Confirm route signals are emitted on success and failure.
4. Confirm local-only / privacy hard stops across all endpoints.
5. Verify agentgateway sidecar fallback with real traffic.
6. Keep dashboard and ops summaries in lockstep with routing state.

That sequence gives the fastest proof that providers and model endpoints are being treated as first-class swarm elements.

---

## 5. Validation checklist

### auto-assign
- [ ] scheduler starts cleanly
- [ ] assignment schema is versioned
- [ ] capability-based scoring is explainable
- [ ] writes to AssistX / Neo4j are idempotent
- [ ] privacy gating works

### auto-router
- [ ] provider/model inventory canonicalized
- [ ] route signals emitted + hydrated
- [ ] local-only and privacy hard stops enforced
- [ ] dashboard/ops counts match live state
- [ ] agentgateway sidecar smoke-tested

### paperclip
- [ ] heartbeat works
- [ ] task claim works
- [ ] progress works
- [ ] completion works
- [ ] worker state is visible in the graph

### AssistX / Neo4j
- [ ] docs sync works
- [ ] provenance events are ingested
- [ ] graph queries answer operational questions
- [ ] no prompt/response bodies are stored in route events

---

## 6. Recommended next action

If we continue from here, the best next patch target is auto-router, because it is the only repo we can inspect and patch locally in this snapshot.

Then the order should be:
1. auto-router contract cleanup
2. auto-assign contract alignment
3. paperclip lifecycle alignment
4. AssistX / Neo4j graph sync verification

---

## 7. Short version

- auto-router is most mature and should be the anchor.
- auto-assign should consume capability summaries, not endpoint details.
- paperclip should expose execution lifecycle, not routing policy.
- AssistX / Neo4j should own the persistent graph of everything.

The remaining work is mostly contract unification, live verification, and graph-backed provenance.
