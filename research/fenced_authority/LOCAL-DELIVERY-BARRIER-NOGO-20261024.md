# Local synthetic delivery barrier and remote-revocation race — October 24, 2026

**Research-only; no production integration or provider authorization.**

## Existing evidence not duplicated

Auto-Router research already has PostgreSQL-revoked synthetic chunk checks,
physical x1/Xwing shared-primary trial evidence, a cooperative worker abort
callback, and a default-off stream wrapper. This additional experiment
deliberately tests a DIFFERENT boundary: **queued output and delivery after
locally acknowledged revocation**, rather than database acceptance alone.

## What this research fixture measures

The synchronous, fake-sink-only LocalDeliveryBarrier stages a bounded
queue of byte strings in a single Python process. Buffering cannot release
bytes. Before releasing ONE queued synthetic chunk, it calls a resource
snapshot callback. A local lock serializes that check, the simulated
delivery callback, and a local revoke acknowledgement. A revoke drops
remaining queued data and invokes the synthetic cancellation callback once.

Experiments show:
- Local revoke before delivery discards the queued chunk.
- A local revoke acknowledgement cannot finish during an in-progress
  fake sink callback; **after acknowledgement**, no additional local
  delivery can begin.
- A stale revision, wrong epoch, revocation, missing authority or partition
  denies the next fake delivery and clears the queue.
- An ambiguous fake transport failure is marked possibly accepted and
  must NEVER be replayed; authority failure before sink is **not** marked
  delivered or possibly accepted.
- A local high-water regression is detected without granting fresh rights.

## Critical counterexamples reproduced — NO-GO

1. The snapshot callback can return a valid state immediately BEFORE an
   independent remote revocation; fake delivery then proceeds even though
   the remote authority is revoked. The local lock does not close this
   cross-resource time-of-check/time-of-effect race.
2. A local revocation on worker A leaves an independent cloned worker B
   with stale authority still able to deliver a synthetic chunk.
3. A fake sink already executing before the local revoke acknowledgement
   may finish before that acknowledgement. This is not an external
   streaming-provider cancellation proof.
4. These experiments have no actual producer, GPU inference, network
   transport, signed trust root, distributed epoch, SQL effect grant, or
   independent trace-witness custody.

Every evidence dictionary marks:
- provider_calls = 0
- sql_executed = False
- provider_execution_authorized = False
- remote_revocation_atomicity_proven = False
- noncopyable_highwater_proven = False

A PASS means the LOCAL synthetic barrier behaves as specified, **NOT**
that live model output is revocable or tenant-safe.

## Next gate

The true model-serving resource must atomically verify a noncopyable,
monotonic high-water revocation state when it accepts each physical effect,
must stop/drain in-flight work with an independently witnessed receipt,
and must survive provider retry, reconnect, streaming, restart and a
competing/partitioned replica. Default-off source code and synthetic
callbacks cannot establish those facts.

Keep Auto-Router PR #75 DRAFT, knowledge issue #57 OPEN, and autonomous
shared-provider model dispatch on HOLD.
