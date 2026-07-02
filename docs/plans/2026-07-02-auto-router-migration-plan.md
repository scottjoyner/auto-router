# Auto-Router Migration Plan

> For Hermes: use this as the docs-first migration sequence before changing code. Work top-down: reconcile docs, lock contracts with tests, then move ownership boundaries in code.

**Goal:** Separate auto-router's routing/control-plane responsibilities from auto-assign's assignment governance and AssistX/Neo4j's canonical task state, while keeping the live router stable during the cutover.

**Architecture:** auto-router keeps provider/model/service discovery, routing decisions, and provenance/event emission. AssistX/Neo4j remains the canonical task and timeline store. auto-assign owns claims, leases, worker placement, and scheduler semantics. The fleet dispatcher logic currently living in auto-router should be treated as transitional execution-consumer code until it is either moved or explicitly demoted out of the router's authority boundary.

**Tech Stack:** FastAPI, SQLite outbox/ledger, Neo4j projection, Docker Compose, systemd, pytest, Jinja dashboard templates.

---

## Current verified state from repo review

The repo already has the following in place:
- OpenAI-compatible routing endpoints
- provider/model/live inventory discovery
- service scan persistence
- CLI discovery persistence
- durable event outbox and AssistX dispatch
- backlog dry-run selection and events
- ops/dashboard surfaces

The current migration risk is not missing features; it is overlapping ownership and a few stale contract names:
- auto-router still carries execution-dispatcher logic
- auto-router can emit or persist some state that should be canonicalized in AssistX/Neo4j
- docs still describe some seams as missing even though code now implements them
- some docs still say `route.selected` where the implemented outbox envelope is `router.route_decision`

The immediate docs work is to make the boundary explicit and normalize event names before moving code.

---

## Phase 0: reconcile the docs before code changes

### Task 0.1: Write the canonical boundary map

**Objective:** Create one docs page that states which repo owns which part of the workflow.

**Files:**
- Create: `docs/ROUTER_ASSISTX_AUTO_ASSIGN_BOUNDARIES.md`
- Modify: `README.md`
- Modify: `docs/LLD.md`
- Modify: `docs/SWARM_UNIFICATION_EXECUTION_PLAN.md`

**Content to include:**
- auto-router owns routing, provider/model inventory, service discovery, CLI discovery, and event emission
- AssistX/Neo4j owns canonical task state, trace timeline, and durable provenance
- auto-assign owns claim/release/lease/heartbeat/placement semantics
- paperclip owns worker execution and completion
- fleet dispatcher code inside auto-router is transitional, not a new canonical authority

**Verification:**
- README docs index points to the new boundary map
- LLD and execution plan no longer imply router ownership of assignment governance
- the boundary map can be read without any external context

**Expected result:** a reader can answer “who owns what?” in under a minute.

---

### Task 0.2: Refresh the migration plan index

**Objective:** Make the migration plan discoverable from the main docs entry points.

**Files:**
- Modify: `README.md`
- Modify: `docs/TODO.md` if it is still used as the active backlog index
- Modify: `docs/OPERATOR_RUNBOOK.md` if there is a restart-check section that references old ownership assumptions

**Content to include:**
- link to `docs/plans/2026-07-02-auto-router-migration-plan.md`
- short note that docs must be updated before any code move
- pointer to the boundary map for ownership questions

**Verification:**
- a fresh reader starts from README and reaches the migration plan within one click
- the plan is visible alongside the existing swarm-unification docs

---

## Phase 1: lock contracts with tests and doc references

### Task 1.1: Define the stable contract surfaces

**Objective:** Make sure the docs name the same contract shapes the code uses.

**Files:**
- Modify: `docs/NEO4J_ASSISTX_INTEGRATION.md`
- Modify: `docs/SERVICE_DISCOVERY.md`
- Modify: `docs/AGENT_SKILLS.md` if any CLI-discovery or worker-usage constraints need to be restated
- Modify: `src/auto_router/README.fleet_task_dispatcher.md`

**Content to include:**
- metadata-only provenance payloads
- canonical IDs for provider/model/service/node/task/assignment
- idempotency expectations for outbox events
- note that prompt bodies and response bodies must not be treated as canonical state

**Verification:**
- each doc points to the same ID and envelope vocabulary
- no doc says the router is the canonical task store

---

### Task 1.2: Add or update tests around the boundary assumptions

**Objective:** Ensure the current router behavior is pinned before moving any responsibilities.

**Files:**
- Modify: `tests/test_route_events.py`
- Modify: `tests/test_event_dispatcher.py`
- Modify: `tests/test_backlog_scheduler.py`
- Modify: `tests/test_ops_dashboard_routes.py`
- Modify: `tests/test_assistx_tasks.py`

**Coverage to assert:**
- route/service/CLI/backlog events stay metadata-only
- the dashboard reflects the same canonical counts as the router state
- backlog dry-run does not become assignment governance
- the event outbox remains idempotent across restarts

**Verification:**
- targeted pytest run passes for the boundary assertions
- no test depends on router-side canonical task ownership

---

## Phase 2: move execution authority out of auto-router

### Task 2.1: Demote or relocate the fleet dispatcher

**Objective:** Stop the router from being the scheduling authority for worker placement.

**Files:**
- Modify: `src/auto_router/fleet_task_dispatcher_service.py`
- Modify: `src/auto_router/fleet_task_dispatcher.py`
- Modify: `src/auto_router/main.py`
- Modify: `docker-compose.yml`
- Modify: `src/auto_router/fleet_task_dispatcher_service.service`
- Modify: any systemd unit or deploy script that references the dispatcher module by the old name

**Implementation options:**
1. Move the dispatcher into the worker/assignment repo and keep only a consumer or adapter here.
2. Keep it in auto-router temporarily, but explicitly label it transitional and ensure it does not own assignment policy.

**Preferred direction:** move ownership out of auto-router.

**Verification:**
- the router still serves routing and inventory endpoints
- worker placement logic no longer appears as a canonical router responsibility in docs or startup flows
- duplicate service names are removed or clarified

---

### Task 2.2: Align the assigner boundary

**Objective:** Make sure auto-assign becomes the only place that owns claim/release/lease behavior.

**Files:**
- Modify: `docs/ROUTER_ASSISTX_AUTO_ASSIGN_BOUNDARIES.md`
- Modify: `docs/SWARM_UNIFICATION_EXECUTION_PLAN.md`
- Modify: auto-assign repo docs and tests once that repo is opened

**Content to include:**
- router emits decisions and facts
- auto-assign consumes those decisions and manages worker lifecycle
- AssistX/Neo4j remains canonical for task state and history

**Verification:**
- the boundary doc reads like a clean handoff, not a shared ownership story

---

## Phase 3: make AssistX/Neo4j the canonical sink

### Task 3.1: Harden provenance and projection write-back

**Objective:** Ensure all router-side state changes are mirrored into AssistX/Neo4j as metadata-only facts.

**Files:**
- Modify: `src/auto_router/event_outbox.py`
- Modify: `src/auto_router/event_dispatcher.py`
- Modify: `src/auto_router/route_events.py`
- Modify: `src/auto_router/assistx_routes.py`
- Modify: `docs/NEO4J_ASSISTX_INTEGRATION.md`

**Content to include:**
- canonical event shapes for route/service/CLI/backlog signals
- idempotency keys and retry/dead-letter handling
- explicit note that local router state is a cache/projection, not the source of truth

**Verification:**
- provenance can be replayed safely
- downstream graph writes are deterministic
- the docs explain how to recover from partial dispatch

---

### Task 3.2: Update the operational docs to match the new authority model

**Objective:** Keep runbooks and deployment docs aligned with the migrated boundary.

**Files:**
- Modify: `docs/PRODUCTION_DEPLOYMENT.md`
- Modify: `docs/OPERATOR_RUNBOOK.md`
- Modify: `docs/DASHBOARD.md`
- Modify: `docs/SWARM_UNIFICATION_CHECKLIST.md` if it still exists as an active checklist

**Content to include:**
- which health checks prove the router is healthy versus which checks prove assignment is healthy
- what to inspect after restart when the dispatcher is quiet or noisy
- what counts as a router issue versus an auto-assign issue

**Verification:**
- restart runbooks no longer imply that one healthy endpoint proves the whole stack is healthy
- operators can distinguish routing failures from assignment failures

---

## Phase 4: finish with code cleanups and deprecation

### Task 4.1: Remove stale router-owned concepts

**Objective:** Delete or rename code paths that imply the router still owns assignment governance.

**Files:**
- Modify: `src/auto_router/backlog_scheduler.py`
- Modify: `src/auto_router/agent_jobs.py`
- Modify: `src/auto_router/agent_workers.py`
- Modify: any docs or templates that still label the router as the scheduler/assignment engine

**Verification:**
- no public docs or admin pages present the router as the assignment authority
- the remaining router surfaces are clearly routing/inventory/provenance surfaces

---

### Task 4.2: Retire the transitional labels

**Objective:** Remove temporary wording once the move is done.

**Files:**
- Modify: `docs/ROUTER_ASSISTX_AUTO_ASSIGN_BOUNDARIES.md`
- Modify: `README.md`
- Modify: `docs/LLD.md`
- Modify: `docs/SWARM_UNIFICATION_EXECUTION_PLAN.md`

**Verification:**
- the docs read as a steady-state architecture, not a migration narrative

---

## Suggested implementation order

1. Add the boundary map and plan links in docs.
2. Update the LLD and execution plan so they agree on ownership.
3. Lock the current behavior with tests.
4. Move or demote the fleet dispatcher.
5. Tighten AssistX/Neo4j provenance as the canonical sink.
6. Update runbooks and deployment docs.
7. Remove transitional wording and stale ownership assumptions.

---

## Done criteria

The migration is far enough along when:
- the docs describe one clear owner per responsibility
- auto-router is clearly routing/inventory/provenance, not assignment governance
- auto-assign is clearly the placement authority
- AssistX/Neo4j is clearly the canonical task timeline
- the fleet dispatcher is no longer a hidden second control plane
- restart verification in the docs matches the actual health and ops surfaces

---

## Near-term next step

Patch the docs first, then run the smallest test set that proves the current router behavior still matches the documented boundary.
