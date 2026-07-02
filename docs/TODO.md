# TODO Backlog

## Recently completed

- Resolved runtime/test merge-conflict debris from the router core.
- Added Cerebras as `cerebras-wse3`, a first-class free API flash-start node.
- Added `auto/flash-start` to the policy engine and `/v1/models` logical aliases.
- Added `flash_start_planner` policy profile and `flash_planning` draft-stage scoring boost.
- Added dashboard visibility for flash-start purpose, recent activity, quota rows, and recent usage highlighting.
- Added service registry support in context projection: top-level services, node services, and provider services.
- Added service launchpad cards and node/provider service links in the dashboard.
- Added opt-in local/private service scanner with external probing disabled by default.
- Added durable SQLite service scan history and startup hydration.

## P0 - Stabilize the current repo

### P0.1 Resolve broken merge state

- [x] Remove all `<<<<<<<`, `=======`, and `>>>>>>>` conflict markers from runtime and test files.
- [x] Keep the newer context/dashboard/agent-job work while preserving circuit breaker and durable ledger behavior.
- [ ] Run `python -m py_compile` over `src/auto_router/*.py` in a checked-out environment.
- [ ] Run `pytest` once dependencies are installed.

### P0.2 Keep OpenAI-compatible routing functional

- [x] Verify `/v1/models` returns concrete provider aliases plus `auto/*` aliases in code/config.
- [ ] Verify `/v1/chat/completions` routes through selected provider and local fallback against live configs.
- [ ] Verify streaming returns provider/model/stage/profile headers against a live provider.
- [ ] Verify `/v1/embeddings` requires `embeddings` capability against a configured embedding provider.

### P0.3 Lock down local-only safety

- [ ] Add tests for `metadata.local_only=true`, `priority=local_only`, and `allow_cloud=false`.
- [ ] Add sensitive marker detection for `voice_auth`, `enrollment_sample`, `private_data`, and `secrets`.
- [ ] Add dashboard warning when a cloud provider is selected for a request with unknown privacy.

## P1 - Router switching mechanism

### P1.1 Implement logical profiles

- [x] Add and test `auto/sophia -> sophia_realtime`.
- [x] Add and test `auto/backlog-burn -> backlog_burn`.
- [x] Add and test `auto/flash-start -> flash_start_planner`.
- [x] Add and test `auto/high-quality -> high_priority_deliverable` path through existing classifier behavior.
- [x] Add and test concrete model alias routing when graph context does not block the provider.

### P1.2 Add explainable routing decisions

- [x] Return `auto_router.provider`, `auto_router.model`, `auto_router.stage`, `auto_router.profile`, and `auto_router.latency_ms` in non-streaming responses.
- [ ] Add structured skip reasons internally for quota exhaustion, privacy denial, missing capability, health failure, and open circuit.
- [x] Expose recent route decisions at `/admin/usage`.

### P1.3 Improve quota reservations

- [x] Keep Redis-backed reservations as the production target.
- [x] Add reserve release on provider error when no usage was recorded.
- [ ] Add reset metadata to dashboard quota rows.
- [ ] Add burn-window mode calculation: `preserve`, `balanced`, `aggressive_burn`.

### P1.4 Cerebras flash-start lane

- [x] Add provider config for `cerebras/flash-reasoner` and `cerebras/glm-4.7-preview`.
- [x] Add graph/context node `cerebras-wse3`.
- [x] Add `flash_start_planner` profile.
- [x] Add runtime classifier support for `auto/flash-start`.
- [x] Add scoring boost for `flash_planning` draft stages.
- [x] Advertise `auto/flash-start` from `/v1/models`.
- [x] Add dashboard card and recent usage highlighting.
- [x] Add runtime provider `/models` refresh/cache via `main_live` wrapper.
- [ ] Add `flash_triage_only` scheduler mode.

## P2 - Neo4j/AssistX integration

### P2.1 Context projection consumer

- [x] Support `AUTO_ROUTER_CONTEXT_CONFIG=http://assistx:8000/api/router/context-projection`.
- [x] Refresh projection in a background loop.
- [x] Show revision/source in `/health`, `/admin/context`, and dashboard.
- [x] Fall back to YAML/bootstrap context when AssistX is unreachable.
- [x] Support service registry projection from AssistX/Neo4j.

### P2.2 AssistX event write-back

- [x] Add an outbox table for router events.
- [x] Emit `router.route_decision` and `router.execution_stage.completed` / `router.execution_stage.failed` events.
- [x] Emit `router.service_snapshot.recorded` events from service scan results.
- [x] Add idempotency keys based on request/stage/provider/model/service/check timestamp.
- [x] Add retry with exponential backoff and dead-letter state.

Current gap: align the AssistX consumer/schema and the remaining docs so they use the implemented outbox envelope names rather than the older `route.selected` wording.

### P2.3 Neo4j schema alignment

- [x] Add docs for `RouterProvider`, `RouterModel`, `RouterDecision`, `QuotaSnapshot`, and `Service`.
- [ ] Add migrations/schema scripts once AssistX event sink shape is finalized.
- [ ] Link `Task -> RouterDecision` and `AgentRun -> RouterDecision` through AssistX write-back.
- [x] Store quota snapshots without prompt bodies in the local ledger/dashboard path.
- [x] Store service scan snapshots locally in SQLite.

## P3 - Backlog burn-down scheduler

### P3.1 Scheduler MVP

- [ ] Poll AssistX for eligible `batch`/`background` tasks.
- [ ] Skip sensitive/local-only work.
- [ ] Enforce critical reserve before scheduling.
- [ ] Queue jobs using `auto/backlog-burn`, `auto/flash-start`, or `/jobs/agent`.

### P3.2 Burn-down strategy

- [ ] Add provider-specific reset windows.
- [ ] Add target daily burn curve.
- [ ] Add surplus release window in the evening.
- [ ] Add final reserve protection for realtime/Sophia requests.

### P3.3 Backlog result handling

- [ ] Write completed summaries/artifact refs back to AssistX.
- [ ] Mark failed jobs retryable or terminal based on failure class.
- [ ] Surface skipped jobs with reasons.

## P4 - Agent worker execution

### P4.1 Sandbox hardening

- [ ] Clone/copy repos into ephemeral worktrees.
- [ ] Deny write/commit/push by default.
- [ ] Add command allow-list enforcement.
- [ ] Capture patch, stdout, stderr, and test output artifacts.

### P4.2 Worker selection

- [ ] Treat Codex as premium repo-critical capacity.
- [ ] Treat Gemini CLI as large-context review/analysis capacity.
- [ ] Treat OpenCode as local/default agent shell.
- [ ] Track Copilot-style monthly usage manually until an API is available.

## P5 - Operations and dashboard

### P5.1 Dashboard upgrades

- [x] Add provider lane cards: `local`, `free_api`, `paperclip`, `blocked` through context/provider tables.
- [x] Add service launchpad with clickable URLs.
- [x] Add service status scan button and dashboard status counts.
- [ ] Add local-vs-cloud request split.
- [x] Add quota burn-down progress.
- [ ] Add quota reset times.
- [ ] Add open circuit cards and retry-after timers.
- [ ] Add backlog queue and burn scheduler status.

### P5.2 Deployment hardening

- [ ] Add `.env.example` entries for AssistX context/event URLs.
- [ ] Add Docker healthcheck for `/health`.
- [ ] Add Prometheus metrics for quota, route count, fallback count, flash-start usage, service status, and circuits.
- [ ] Add secure secret handling guidance.
