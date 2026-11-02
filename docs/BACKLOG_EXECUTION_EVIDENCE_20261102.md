# Backlog selection versus execution evidence (2026-11-02)

`/admin/backlog/dry-run` selects an eligible provider candidate and may enqueue a **selection event**. `/admin/backlog/burn-down` additionally delivers those events through the router outbox. Neither endpoint claims an authoritative AssistX Task nor performs an inference request, so neither provides a verified generation or completion receipt. A successful event delivery is never itself a token or a completed task.

The additive `execution` response field explicitly identifies this limitation for both endpoints, without breaking existing `summary`, `decisions`, or `dispatch` fields. This change is a reporting/contract safety correction, not an activation of continuous job execution.

To admit productive work later, require: canonical signed Task and owner scope; fresh runtime projection; authenticated provider transport; bounded concurrency and interactive reserve; claim fencing and idempotency; runtime generation counters or completion receipt with actual measured usage; durable validated result bound to the claimed Task. Do not weaken those gates to increase raw utilization.
