# Swarm Unification Checklist

This checklist breaks the swarm work down repo by repo and evaluates what is already up, what is partially up, and what is still missing.

For the execution-ready version of this plan, see:
- `docs/SWARM_UNIFICATION_EXECUTION_PLAN.md`

Scope:
- auto-assign
- auto-router
- paperclip
- AssistX / Neo4j

Note:
- In this local checkout, only `~/git/auto-router` is present under `/home/scott/git`.
- `auto-assign`, `paperclip`, and `auto-assist` / AssistX repos were not present in this filesystem snapshot, so their status below is inferred from prior design docs and earlier session context rather than live file inspection.

## 1) auto-assign

Role in the swarm:
- task intake and normalization
- assignment scoring and ownership
- queue selection and scheduling
- policy-aware task placement
- emits assignment / heartbeat / completion events

What should be up:
- stable task/assignment event schema
- backlog and readiness classification
- worker/candidate scoring based on capability, load, privacy, and priority
- read-only integration into auto-router for capability/routing hints
- write-back into AssistX / Neo4j for assignment state
- docs explaining how assignments map to swarm elements

Observed status:
- repo not present locally for live inspection
- from prior session context, auto-assign docs exist and describe scheduler / heartbeat / approval / explainable routing
- the integration contract appears designed, but deployment status is unclear and likely incomplete

What is up already:
- the conceptual boundary is well defined: auto-assign is the placement brain, not the transport/router
- event contracts and integration docs exist in prior context

Gaps:
- no locally verified running service
- no live check of scheduler tick / queue behavior
- no live proof of assignment events reaching AssistX / Neo4j
- no evidence of a deployed task-claim or worker-heartbeat loop in this snapshot

Checklist:
- [ ] Confirm repo checkout exists on the target node
- [ ] Verify scheduler or daemon startup path
- [ ] Confirm assignment event schema is current and versioned
- [ ] Verify worker capability model includes local/cloud/privacy tags
- [ ] Verify read-only queries to auto-router are working
- [ ] Verify writes back to AssistX/Neo4j are idempotent
- [ ] Add or verify tests for scheduling, stale leases, and privacy gating
- [ ] Add or verify docs for swarm-element mapping

## 2) auto-router

Role in the swarm:
- execution router and control plane
- provider/model endpoint selection
- privacy, quota, circuit breaker, and fallback enforcement
- live model / provider registry projection
- route signal emission and outbox write-back
- dashboard and operator surface

What is already up:
- repository exists locally and is actively modified
- OpenAI-compatible API surface is implemented: chat, responses, embeddings, completions, models
- dashboard and ops routes exist
- context projection from AssistX/Neo4j is documented and implemented in the codebase
- live model discovery, model registry snapshots, service registry/scanning, CLI discovery, backlog dry-run, and outbox dispatch are present
- provider/model swarm unification is already partially in place in docs and dashboard summaries
- agentgateway sidecar support is now present in docs, config, and code paths
- tests exist for config, policy, live models, ops dashboard, route events, and agentgateway integration pieces

What is partially up:
- provider and model endpoints are being treated as first-class swarm elements in docs, dashboard summaries, and live registry paths
- route signals are being projected into context and ops summaries
- agentgateway is wired conceptually and partially in code, but the sidecar deployment and fallback policy still need end-to-end validation

Gaps in auto-router:
- the worktree is still dirty; changes are not yet clearly consolidated
- live proof that every provider/model endpoint alias is canonicalized into one swarm inventory is still needed
- the older implementation plan still calls out items that may not be fully closed:
  - hardened local-only guarantees across every code path
  - full AssistX event write-back for all route outcomes
  - backlog scheduler execution beyond dry-run
  - complete worker-plane integration for task execution
- dashboards may show swarm state, but we still need to verify that the UI and API expose the same counts/IDs everywhere
- if agentgateway is enabled, fail-open/fail-closed behavior needs a full smoke pass with real requests

Checklist:
- [x] Keep OpenAI-compatible routes stable
- [x] Keep quota and usage ledger durable
- [x] Keep context projection and live model registry in the same snapshot
- [x] Keep provider / model / signal summaries visible in dashboard and ops views
- [x] Keep agentgateway docs and partial integration in place
- [ ] Verify all provider aliases collapse to canonical swarm IDs
- [ ] Verify every `/models`-capable provider is projected into live inventory
- [ ] Verify route signal emission and hydration on both success and failure paths
- [ ] Verify local-only / privacy hard stops on every route path
- [ ] Verify AssistX write-back payloads stay metadata-only
- [ ] Verify agentgateway sidecar with real traffic and fallback behavior
- [ ] Close any remaining implementation-plan items that are still only documented

## 3) paperclip

Role in the swarm:
- worker execution plane
- claim/check-out/check-in loop
- heartbeat and load reporting
- task progress / completion
- actual agent workforce management

What should be up:
- worker registration and identity
- health/heartbeat API
- task queue/claim semantics
- execution sandbox / allow-listing if tasks can run code
- explicit relationship to auto-assign assignments
- progress and completion events back to AssistX / Neo4j

Observed status:
- no local `paperclip` repo was found in this filesystem snapshot
- earlier session context indicates Paperclip exists and is used as the orchestrator/execution surface
- the integration is conceptually central, but live implementation status is not verified here

What is up already:
- the role is clearly defined in the current plan: paperclip is the workforce/execution layer, not the router
- earlier context indicates it is already running on at least one host

Gaps:
- no local source inspection available here
- no verified contract for task claiming / heartbeats / completion events in this snapshot
- no proof that worker state is being mirrored into the shared graph consistently

Checklist:
- [ ] Verify repository or deployment location on the target host
- [ ] Verify worker heartbeat endpoint and schema
- [ ] Verify claim / release semantics
- [ ] Verify task-progress reporting
- [ ] Verify mapping from assignment IDs to worker runs
- [ ] Verify provenance events for claimed / completed work
- [ ] Verify any sandbox allow-list and write/commit restrictions

## 4) AssistX / Neo4j

Role in the swarm:
- canonical graph memory and system state
- task lifecycle authority
- routing / provider / model context projection
- provenance and audit trail
- documentation and knowledge sync

What is already up:
- auto-router already consumes AssistX-style context projection and exposes it in docs and dashboard state
- route provenance outbox exists on the router side
- earlier session context shows the graph-backed coordination model is already part of the system vision
- docs and knowledge sync into Neo4j are part of the broader workflow

What is partially up:
- router-side context projection, service inventory, and live model inventory are visible
- router dashboard surfaces graph-style summaries and signal counts
- AssistX integration endpoints are documented and partly wired from the router perspective

Gaps:
- this snapshot does not show a live AssistX repo checkout for direct verification
- no live validation of graph write-back or documentation sync was performed in this turn
- the exact schema alignment between route events, assignments, worker runs, and Neo4j nodes needs one consolidated contract document

Checklist:
- [ ] Verify AssistX projection endpoint is live and signed/authorized as intended
- [ ] Verify route events are arriving in the graph sink
- [ ] Verify docs sync includes all target repos and not just router-local markdown
- [ ] Verify canonical graph nodes for task, assignment, provider, model, worker, node, and route decision
- [ ] Verify replay/idempotency strategy for provenance ingestion
- [ ] Verify operator queries can answer "why did this run here?" and "what is blocked right now?"

## 5) Cross-repo checklist

This is the actual unification layer that matters.

### A. Identity and canonicalization
- [ ] Providers have stable provider IDs
- [ ] Models have stable provider-scoped model IDs
- [ ] Worker nodes have stable node IDs
- [ ] Assignment and route IDs are stable and correlated
- [ ] Aliases collapse into canonical IDs instead of duplicating records

### B. Capability and policy mapping
- [ ] auto-assign reasons in task/capability terms
- [ ] auto-router reasons in provider/model/endpoint terms
- [ ] paperclip reasons in worker/queue/heartbeat terms
- [ ] AssistX stores the joined state in graph form
- [ ] privacy/local-only rules are enforced before any cloud-backed route

### C. Event flow
- [ ] task created
- [ ] assignment recommended
- [ ] route decided
- [ ] worker claimed
- [ ] work completed
- [ ] provenance written back
- [ ] all events are metadata-only, no prompt or response bodies

### D. Observability
- [ ] dashboard shows provider count and model count together
- [ ] dashboard shows node/worker health together with route signals
- [ ] ops summary exposes drift, latency, and recent probes
- [ ] outbox backlog is visible
- [ ] fallback/fail-open behavior is visible

### E. Delivery gaps
- [ ] missing local repo checkouts restored where needed
- [ ] deployment parity confirmed across nodes
- [ ] docs synchronized to Neo4j
- [ ] tests added or updated for the live contract
- [ ] one end-to-end smoke path exercised: trigger -> assign -> route -> execute -> record

## 6) Priority ordering

If you want the shortest path to completion, do it in this order:
1. Lock down canonical IDs and event schemas.
2. Verify auto-router live inventory + signal hydration.
3. Verify auto-assign scheduling and assignment emission.
4. Verify paperclip claim/execute/heartbeat loop.
5. Verify AssistX / Neo4j write-back and doc sync.
6. Run one end-to-end smoke pass.

## 7) Current bottom line

- auto-router is the furthest along in this local snapshot.
- auto-assign is conceptually defined but not locally verified.
- paperclip is essential but not locally verified here.
- AssistX / Neo4j is the memory backbone, but graph-write validation still needs a real run.

The plan is good, but the remaining work is mostly contract completion, live verification, and end-to-end proof that the swarm elements are being treated as one inventory rather than separate systems.