# TODO Backlog

## P0 - Stabilize the current repo

### P0.1 Resolve broken merge state

- Remove all `<<<<<<<`, `=======`, and `>>>>>>>` conflict markers from runtime and test files.
- Keep the newer context/dashboard/agent-job work while preserving circuit breaker and durable ledger behavior.
- Run `python -m py_compile` over `src/auto_router/*.py`.
- Run `pytest` once dependencies are installed.

### P0.2 Keep OpenAI-compatible routing functional

- Verify `/v1/models` returns concrete provider aliases plus `auto/*` aliases.
- Verify `/v1/chat/completions` routes through selected provider and local fallback.
- Verify streaming returns provider/model/stage/profile headers.
- Verify `/v1/embeddings` requires `embeddings` capability.

### P0.3 Lock down local-only safety

- Add tests for `metadata.local_only=true`, `priority=local_only`, and `allow_cloud=false`.
- Add sensitive marker detection for `voice_auth`, `enrollment_sample`, `private_data`, and `secrets`.
- Add dashboard warning when a cloud provider is selected for a request with unknown privacy.

## P1 - Router switching mechanism

### P1.1 Implement logical profiles

- Add and test `auto/sophia -> sophia_realtime`.
- Add and test `auto/backlog-burn -> backlog_burn`.
- Add and test `auto/high-quality -> high_priority_deliverable`.
- Add and test concrete model alias routing when graph context does not block the provider.

### P1.2 Add explainable routing decisions

- Return `auto_router.provider`, `auto_router.model`, `auto_router.stage`, `auto_router.profile`, and `auto_router.latency_ms` in non-streaming responses.
- Add skip reasons internally for quota exhaustion, privacy denial, missing capability, health failure, and open circuit.
- Expose recent route decisions at `/admin/usage`.

### P1.3 Improve quota reservations

- Keep Redis-backed reservations as the production target.
- Add reserve release on provider error when no usage was recorded.
- Add reset metadata to dashboard quota rows.
- Add burn-window mode calculation: `preserve`, `balanced`, `aggressive_burn`.

## P2 - Neo4j/AssistX integration

### P2.1 Context projection consumer

- Support `AUTO_ROUTER_CONTEXT_CONFIG=http://assistx:8000/api/router/context-projection`.
- Refresh projection in a background loop.
- Show revision/source in `/health`, `/admin/context`, and dashboard.
- Fall back to YAML/bootstrap context when AssistX is unreachable.

### P2.2 AssistX event write-back

- Add an outbox table for router events.
- Emit `router.execution_stage.completed` and `router.execution_stage.skipped` events.
- Add idempotency keys based on request/stage/provider/model.
- Add retry with exponential backoff and dead-letter state.

### P2.3 Neo4j schema alignment

- Add docs and migrations for `RouterProvider`, `RouterModel`, `RouterDecision`, and `QuotaSnapshot`.
- Link `Task -> RouterDecision` and `AgentRun -> RouterDecision`.
- Store quota snapshots without prompt bodies.

## P3 - Backlog burn-down scheduler

### P3.1 Scheduler MVP

- Poll AssistX for eligible `batch`/`background` tasks.
- Skip sensitive/local-only work.
- Enforce critical reserve before scheduling.
- Queue jobs using `auto/backlog-burn` or `/jobs/agent`.

### P3.2 Burn-down strategy

- Add provider-specific reset windows.
- Add target daily burn curve.
- Add surplus release window in the evening.
- Add final reserve protection for realtime/Sophia requests.

### P3.3 Backlog result handling

- Write completed summaries/artifact refs back to AssistX.
- Mark failed jobs retryable or terminal based on failure class.
- Surface skipped jobs with reasons.

## P4 - Agent worker execution

### P4.1 Sandbox hardening

- Clone/copy repos into ephemeral worktrees.
- Deny write/commit/push by default.
- Add command allow-list enforcement.
- Capture patch, stdout, stderr, and test output artifacts.

### P4.2 Worker selection

- Treat Codex as premium repo-critical capacity.
- Treat Gemini CLI as large-context review/analysis capacity.
- Treat OpenCode as local/default agent shell.
- Track Copilot-style monthly usage manually until an API is available.

## P5 - Operations and dashboard

### P5.1 Dashboard upgrades

- Add provider lane cards: `local`, `free_api`, `paperclip`, `blocked`.
- Add local-vs-cloud request split.
- Add quota burn-down progress and reset times.
- Add open circuit cards and retry-after timers.
- Add backlog queue and burn scheduler status.

### P5.2 Deployment hardening

- Add `.env.example` entries for AssistX context/event URLs.
- Add Docker healthcheck for `/health`.
- Add Prometheus metrics for quota, route count, fallback count, and circuits.
- Add secure secret handling guidance.
