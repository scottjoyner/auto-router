# Cache Affinity Identity PoC — 2026-08-17

Issue: `#12`
Branch: `agent/cache-affinity-poc`

## Hypothesis

Before moving KV blocks across nodes, auto-router can gain measurable repeated-prefix/session locality by tracking a strict cache identity. Cache affinity must remain weaker than admission, health, capacity, lease, priority, and path constraints.

## Implemented baseline

Added `CacheIdentity` with:
- model hash;
- quantization;
- context size;
- runtime ID;
- session ID;
- SHA-256 stable-prefix fingerprint.

Added an experimental `affinity_score()` that returns zero when model hash, quant, context size, or runtime ID differ. Compatible candidates receive a small base score, with additional weight for same-session and same-prefix matches.

This module is **not wired into production routing yet**. It provides a deterministic scoring primitive for benchmarks first.

## Tests

Coverage verifies:
- exact session+prefix match scores highest;
- compatible runtime with different session/prefix receives only base affinity;
- model-hash mismatch forces zero;
- quant mismatch forces zero;
- context-size mismatch forces zero;
- runtime mismatch forces zero.

## Next experiment

1. Feed synthetic route candidates through current baseline policy and cache-affinity scoring side-by-side without changing selection.
2. Emit predicted affinity into the AssistX #24 trace contract.
3. Run repeated-prefix and multi-turn workloads against real endpoints.
4. Record TTFT, prompt-processing time, cache hit if exposed, and fallback reason.
5. Only after measured benefit, allow affinity to act as a tie-breaker among otherwise equally admissible/healthy/capable candidates.
6. Test eviction, runtime reload, quant change, model hash change, path failure, and node loss.

## Guardrail

Cache locality never makes an inadmissible, unhealthy, capacity-exhausted, stale, or unauthorized runtime eligible. Runtime/model identity changes invalidate affinity instead of attempting reuse.
