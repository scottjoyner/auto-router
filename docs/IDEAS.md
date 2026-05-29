# Ideas and Future Work

## Router-as-a-resource-scheduler

Treat every model lane as a resource with a purpose:

- free API tokens are expiring daily/monthly inventory;
- local LM Studio models are privacy-preserving baseline capacity;
- Codex/Gemini CLI/OpenCode/Copilot are bounded software-agent work lanes;
- AssistX/Neo4j is the context and task authority;
- Sophia is the realtime input edge.

The router should continuously answer: **what is the highest-value safe work we can run with the quota that would otherwise expire?**

## Backlog burn ideas

- Nightly `doc_review` pass against stale markdown files.
- Generate missing unit-test skeletons for repos with failing coverage thresholds.
- Run judge/repair passes over old implementation plans.
- Summarize recent AssistX tasks into daily operator digests.
- Enrich task nodes with capability tags and estimated implementation size.
- Compare local model answers against free-cloud judge models for calibration.
- Use free fast models for low-risk classification and local models for sensitive content.

## Sophia realtime ideas

- Add `auto/sophia` alias for voice-app responses.
- Keep voice auth/enrollment traffic strictly local-only.
- Route low-risk utility responses to fast local models first.
- Allow safe cloud assist only when graph policy marks the request `safe_cloud`.
- Add dashboard counters for local-vs-cloud Sophia requests.

## Neo4j/AssistX ideas

- Publish `RouterContextProjection` from AssistX every minute.
- Write `RouterDecision` nodes linked to `Task`, `AgentRun`, and `Artifact`.
- Use graph task ranking to choose backlog jobs during quota burn windows.
- Add a `needs_refine` flag for high-priority deliverables drafted locally.
- Add `quota_reserve_class` to task metadata: `protected`, `balanced`, `surplus_only`.

## Safety ideas

- Add a policy simulator endpoint: `POST /admin/policy/simulate`.
- Add dry-run mode for backlog scheduler.
- Add provider kill-switches in the dashboard.
- Add redaction and prompt hashing before any provenance write-back.
- Add regression tests that prove local-only requests never leave local providers.
