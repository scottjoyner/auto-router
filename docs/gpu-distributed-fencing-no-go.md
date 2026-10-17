# Distributed GPU reservations: evidence and production NO-GO

**Status:** Prototype only, not integrated into Auto-Router's request path and not deployed to any GPU node. See draft Auto-Router PR #70.

## Verified reservation behavior

- Redis Lua creates atomic single-holder reservations across independently connected clients and separate Python processes.
- The self-contained script scripts/test_gpu_redis_disposable.sh launches a temporary Redis 7 container bound only to 127.0.0.1:16389, with no durable storage and a removal trap. It never accesses production Redis.
- Unique owner identifiers and sequence numbers prevent a timed-out owner from releasing or renewing another owner's later reservation.
- Transport exceptions, ambiguous results, absent leases and malformed resource identifiers fail closed in the prototype.
- The previously implemented single-router physical GPU gate shares one conservative slot across model servers and signed AssistX projection generations. The distributed prototype is deliberately not wired to it.

## Why distributed reservations alone do NOT authorize production GPU dispatch

1. **Executor bypass.** Raw llama.cpp listeners remain exposed on Tailnet; they do not validate reservation ownership or fencing sequences. An authorized direct caller can bypass the router.
2. **Stale effect after TTL.** Router A can verify its reservation, stall or continue streaming, then send GPU work after its lease expires and Router B obtains another reservation.
3. **Counter reset.** Ephemeral Redis can restart without a persisted fencing counter; tests demonstrate sequence number reuse after simulated state loss. Numeric sequence alone cannot establish a durable global epoch.
4. **Timeout and partition.** Generation can continue after a lease-renewal error unless the GPU execution owner revokes or contains the work.
5. **Identity and custody.** A physical GPU group string in provider YAML is not hardware attestation. A healthy service or its provider-model name is not a tenant/user-bound execution receipt.

## Minimum GPU-owner proxy contract

- A dedicated, authenticated proxy must be the *only* network ingress for the GPU-serving llama.cpp processes. Move raw model HTTP listeners to host-local bindings behind operator-approved ACL changes; do not change the running services as part of this draft.
- GPU owner validates signed model identity, physical PCI/render device, caller delegation, approved request scope, physical-card quota and durable fencing epoch before touching inference state.
- It must maintain the lease for the *entire* request, including model loading, generation, streaming, cancellation and terminal cleanup. A renewal failure must immediately prevent stale work from persisting.
- The authoritative epoch must survive Redis failover/reset; Redis without durable fencing state and enforcement by the GPU owner is insufficient.
- Produce server-minted, tenant- and actor-bound, payload-free attempt receipts. Never equate the first network chunk with a token or the provider selection with an attested runtime.

## Release acceptance

A disposable multi-process, two-user run must prove same-card contention and cross-card independence, expired/stale-owner rejection, Redis failover, old-stream termination, signature/counter rollback rejection, lost-ACK behavior, process crash, network partition, concurrency, key rotation, exact model and hardware identity, and custody/readback isolation. Repeat on R9700 ToolCall/MiniCPM and xwing Bonsai 27B. Keep the original CPU path available for rollback.

**NO-GO:** Keep all four candidate GPU providers disabled and PR #70 in draft until the GPU-enforcing proxy, durable fencing authority, and per-user receipt requirements of issue #37 pass. Admin-security draft PR #41 remains a separate release blocker.
