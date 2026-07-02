# Meaningful Outcomes Roadmap

This document breaks the fleet task pipeline into four implementation phases so the system produces more than raw text. The goal is to keep the current parallel throughput advantage while improving tool use, evidence quality, and operator visibility.

Status legend:
- [ ] not started
- [~] in progress
- [x] complete

## Phase 0 — Scope and documentation
[~] Define the outcome model, the task metadata contract, and the reporting surfaces before changing behavior.

Deliverables:
- [x] Four-phase roadmap documented
- [x] Task categories described in terms of capability, evidence, and outcome quality
- [x] Metrics targets defined for visibility and analysis
- [x] Acceptance criteria written for each phase

## Phase 1 — Telemetry and visibility
[ ] Add task-level telemetry so we can see where time is spent and where quality degrades.

Deliverables:
- queue wait time
- claim-to-start time
- generation latency
- retry count
- token counts
- stage, node, model, role, and queue class
- outcome state and rejection reason
- summary stats by node, model, and task kind

Exit criteria:
- every completed task has a traceable telemetry record
- the dashboard can show throughput and failure patterns by lane
- operators can see whether the bottleneck is queueing, generation, or review

## Phase 2 — Capability-aware routing and evidence bundles
[ ] Separate prompt-only generation from tasks that require tools or external evidence.

Deliverables:
- task_kind field
- requires_tools flag
- evidence_required flag
- allowed_tools or capability hints
- evidence_bundle or evidence_summary field
- routing logic that can send tool-required work to the proper lane

Exit criteria:
- research/evidence tasks are no longer treated like plain drafting jobs
- the router preserves enough metadata to choose the right execution lane later
- workers can receive a compact evidence bundle instead of an empty prompt

## Phase 3 — Outcome scoring and completion semantics
[ ] Score completed work based on usefulness, evidence, and review outcome rather than raw text length.

Deliverables:
- accepted / rejected / needs_more_evidence states
- review reasons
- usefulness score or outcome score
- confidence notes for ambiguous work
- persistence of review outcomes in the graph and stats snapshot

Exit criteria:
- a task can complete without being treated as a high-quality result
- rejected work is explainable
- accepted outputs are distinguishable from merely finished outputs

## Phase 4 — Feedback loop and routing refinement
[ ] Use the telemetry and outcome data to improve future routing decisions.

Deliverables:
- performance reports by node/model/task_kind
- success rate and retry rate by lane
- accepted artifact rate and evidence coverage rate
- routing score updates based on historical results
- operator-facing summaries of what is working and what is not

Exit criteria:
- routing decisions are influenced by observed performance
- the system can show which nodes/models are valuable for which work
- operators can see throughput, quality, and cost tradeoffs over time

## Notes

- The current fleet already does parallel dispatch well.
- The main gap is not throughput; it is outcome quality and visibility.
- Prompt-only workers can remain valuable for drafting and refinement.
- Tool-capable orchestration should be added as a separate lane rather than forced into every worker.
- The reporting goal is to measure accepted value, not just completed volume.
