# Implementation Plan

This plan converts `auto-router` from a limited local-first proxy into the router/scheduler node that can support Sophia realtime requests, AssistX/Paperclip task execution, Neo4j-backed context, and surplus free-quota burn-down.

## P0 - Repository stabilization

### P0.1 Resolve broken merge state

- Remove unresolved conflict markers from `src/auto_router/main.py`, `src/auto_router/providers.py`, `src/auto_router/policy.py`, and `tests/test_policy.py`.
- Preserve the durable `UsageLedger`, circuit breaker, Redis quota fallback, context projection, dashboard fragment, and agent-job endpoints.
- Ensure every updated Python file parses with `python -m py_compile`.
- Acceptance: the app imports and starts without syntax errors.

### P0.2 Restore core HTTP surface

- Keep `GET /health`, `GET /metrics`, `GET /dashboard`, `GET /admin/quota`, `GET /admin/context`, `GET /admin/providers`, `GET /admin/providers/health`, `GET /admin/usage`, `GET /admin/circuits`, and `GET /admin/agent-jobs`.
- Keep `GET /v1/models`, `POST /v1/chat/completions`, `POST /v1/responses`, `POST /v1/embeddings`, and `POST /v1/completions` compatible with LM Studio/OpenAI clients.
- Keep `POST /jobs/agent`, `GET /jobs/agent/{job_id}`, and artifact lookup for agent-worker work.
- Acceptance: a standard OpenAI-compatible client can point `OPENAI_BASE_URL` at the router.

### P0.3 Make local-only guarantees testable

- Treat `metadata.local_only`, top-level `local_only`, `priority=local_only`, and `allow_cloud=false` as hard stops for cloud routing.
- Add privacy markers for `voice_auth`, `enrollment_sample`, `private_data`, `internal_docs`, `personal_docs`, and future `secrets` classification.
- Acceptance: tests prove cloud providers are skipped when local-only constraints are active.

## P1 - Router switching mechanism

### P1.1 Add logical router profiles

- Add `sophia_realtime` profile for `auto/sophia` traffic.
- Add `backlog_burn` profile for safe queued work that should consume expiring free quota.
- Keep `high_priority_deliverable`, `code_high_quality`, `interactive_balanced`, and `local_only`.
- Acceptance: policy tests verify alias-to-profile mapping.

### P1.2 Make routing decisions explainable

- Add `auto_router` response metadata for provider, model, profile, stage, and latency.
- Record successes and failures in the durable ledger without storing prompt bodies.
- Record provider/model circuit failures with retry-after handling.
- Acceptance: `/admin/usage` shows recent route decisions and failures.

### P1.3 Improve ranking and fallback

- Rank by policy profile, lane, capabilities, provider priority, context projection, and stage purpose.
- Prefer local draft for high-priority work and free-cloud refine/judge when privacy allows.
- Prefer low-latency local/fast-free models for Sophia.
- Always keep LM Studio fallback available when cloud quota is exhausted or blocked.
- Acceptance: degraded cloud providers do not break requests that can safely run locally.

## P2 - Neo4j and AssistX integration

### P2.1 Consume AssistX context projection

- Support `AUTO_ROUTER_CONTEXT_CONFIG=http://assistx:8000/api/router/context-projection`.
- Refresh context in the background.
- Surface context revision/source in `/health`, `/admin/context`, and dashboard.
- Treat YAML context as bootstrap fallback, not the source of truth.
- Acceptance: when the projection blocks a provider, the router skips it.

### P2.2 Emit routing provenance to AssistX

- Add an outbox for `router.execution_stage.completed`, `router.execution_stage.failed`, and `router.execution_stage.skipped` events.
- Add idempotency keys based on request ID, stage, provider, and model.
- Post events to AssistX `/api/events` or `/api/router/events` once the endpoint exists.
- Keep prompt bodies redacted; write metadata, hashes, summaries, and artifact refs only.
- Acceptance: AssistX can link `Task -> RouterDecision` and `AgentRun -> RouterDecision`.

### P2.3 Align graph schema

- Add or document `RouterProvider`, `RouterModel`, `RouterDecision`, `QuotaSnapshot`, and `RouterContextProjection` nodes.
- Link providers to models and tasks/runs to decisions.
- Write quota snapshots and circuit states as operational metadata.
- Acceptance: Neo4j can answer “why did this task run on this model?”

## P3 - Backlog burn-down scheduler

### P3.1 Scheduler candidate selection

- Poll AssistX for `READY`/`QUEUED` low-risk `batch` and `background` tasks.
- Skip sensitive/local-only work.
- Prioritize tasks by graph rank, age, and value.
- Acceptance: scheduler can dry-run and list eligible/skipped tasks with reasons.

### P3.2 Free-quota burn policy

- Implement quota modes: `preserve`, `balanced`, and `aggressive_burn`.
- Preserve critical/realtime reserve before burning backlog work.
- Use daily reset windows for request/token/neuron quotas and monthly smoothing for premium quotas.
- Acceptance: no backlog burn occurs below critical reserve.

### P3.3 Result handling

- Write summaries, artifacts, and failure disposition back to AssistX.
- Mark retryable failures separately from terminal failures.
- Attach provider/model/latency/quota metadata to each completed stage.
- Acceptance: backlog jobs leave traceable outcomes in AssistX/Neo4j.

## P4 - Agent worker plane

### P4.1 Sandbox execution

- Prepare ephemeral worktrees for repo tasks.
- Disable write/commit/push unless explicitly allowed.
- Enforce command allow-lists for tests and diagnostics.
- Capture stdout, stderr, patches, and test output artifacts.
- Acceptance: agent jobs can run without mutating canonical repos by default.

### P4.2 Worker selection

- Reserve Codex for `repo_critical` implementation/review.
- Use Gemini CLI for large-context analysis and documentation/test passes.
- Use OpenCode for local terminal-agent workflows.
- Track Copilot-style usage as monthly constrained capacity.
- Acceptance: `/admin/agent-workers` and dashboard show availability and recent jobs.

## P5 - Dashboard and operations

### P5.1 Dashboard upgrades

- Show providers by execution lane: `local`, `free_api`, `paperclip`, `blocked`.
- Show local-vs-cloud request split, quota burn-down, reset windows, and critical reserve.
- Show backlog scheduler mode and recent selected/skipped jobs.
- Show circuit breakers and retry-after timers.
- Acceptance: operator can understand route decisions without reading logs.

### P5.2 Deployment hardening

- Add `.env.example` entries for AssistX context/event URLs and scheduler toggles.
- Add Docker health checks and Prometheus metrics.
- Add production secret handling and prompt-redaction guidance.
- Acceptance: homelab deployment can run router, Redis, SQLite, and optional workers safely.

## Immediate next cycle checklist

1. Run the stabilized app locally with copied example configs.
2. Hit `/health`, `/v1/models`, `/admin/context`, and `/dashboard`.
3. Send one `auto/local` request and one `auto/sophia` request to verify profile selection.
4. Send one blocked-provider context projection and verify skip behavior.
5. Add the AssistX context projection endpoint.
6. Add dry-run backlog scheduler endpoint.
7. Add event outbox and AssistX write-back client.
