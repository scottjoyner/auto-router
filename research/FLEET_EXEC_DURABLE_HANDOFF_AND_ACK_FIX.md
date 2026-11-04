# fleet-exec: durable handoff research and ACK ownership fix

**November 3, 2026 — RESEARCH ONLY. Not deployed, merged, or eligible for production takeover.**

## Exact running source and reproduced ACK defect

The live x1-370 fleet-exec relay (PID 3113) executes
/home/scott/knowledge/20-Projects/fleet-exec/relay.py, SHA256
4904966d9e78a24f2b74a70c34d48febb0dfb399f37bed14ba8b52f748cdfb29.
The device (PID 3107) executes device.py, SHA256
d1b624d4dc97adb2b33bb8b93c05441260f931ba255ed235a06f0044a17afa3b.

The original POST /device/ack handler validated the client's device
certificate identity but never checked whether its call_id was dispatched
to that device. Its Registry.note_ack(call_id) unconditionally inserted
the identifier in the global acknowledged set, influencing requeue policy.

An unchanged copy of the running relay was tested in an isolated off-host
loopback relay using temporary mTLS certificates and tokens. It accepted:

- An ACK for a nonexistent call ID with HTTP 200 (expected HTTP 404).
- An ACK from device joyner for a call queued to x1-370 with HTTP 200
  (expected HTTP 403).

These are reproducible authorization failures in the lab, not evidence
that an unauthorized ACK occurred on the live host.

## Reviewable narrow patch

Path: research/patches/fleet_exec_ack_ownership.patch
Patch SHA256: e679b2b048e8ecb0c8203c60ec0902c725f4dcd318eeda17e7c85d363278b3b0.

The patch requires the call to exist in Registry.queued_for(call_id)
and the assigned device to match the authenticated device before note_ack.
Unknown calls return 404; other-device calls return 403.
Two new isolated mTLS negative tests exercise those exact cases.

Original relay.py SHA256:
4904966d9e78a24f2b74a70c34d48febb0dfb399f37bed14ba8b52f748cdfb29
Patched relay.py SHA256:
f8667d587b650ceab1e092f082360bf94ce9969056cadaf3504c9016eb224ba9
Original relay_test.py SHA256:
64976f8d59c508b83070e94c48d82071a770c45144367f6d894859cfa018bda2
Patched relay_test.py SHA256:
b2f50cbc769d106b3e63befa820fee984107e87f57f687b0f26c15096af2832f

Patch was applied in a disposable directory to exact original files,
then output was compared byte-for-byte to tested copies (PASS).
On both independent off-host machines, xwing and Destroyer, the fixed
relay passed the two negative tests plus an existing positive test
that a legitimate device ACKs before executing an allowed command:
3/3 tests PASS on each host.

**Remaining ACK caveat:** the narrow patch does not establish a durable
ACK-before-side-effect transaction, validate the call was physically
delivered before an ACK, or prevent split-brain across two relays.
Review and broader testing must precede deploying the patch.

## Durable local handoff prototype

Research files: research/fleet_exec_durable_handoff.py and
tests/test_fleet_exec_durable_handoff.py.

Implements a SQLite laboratory journal, NOT a replacement relay,
executing no shell commands and using no fleet credentials or network.

State transitions: QUEUED -> DELIVERED -> ACKED -> RESULT_RECORDED.
On a synthetic monotonic epoch change, every unfinished prior call
is QUARANTINED and never automatically replayed. All mutations use
BEGIN IMMEDIATE transactions, WAL and synchronous FULL. Call ID,
device ID, payload digest, epoch and result digest are matched.
Conflicting replay or stale controller attempts are rejected.

Validation: 27/27 synthetic tests PASS on x1 research scratch,
27/27 PASS on xwing and 27/27 PASS on Destroyer. Tests cover
restart persistence, invalid ACK/result ordering, conflicting
idempotency, local concurrent epoch contention, wrong-device
updates and quarantine. Separate uncoordinated SQLite databases
are explicitly demonstrated able to elect different controllers.

**This is not cross-host fencing.** The prototype's synthetic epoch
acknowledgment is not signed approval. It has no independent custody,
no trusted external high-water, no real mTLS principal binding,
no at-most-once side-effect guarantee and no production admission.

## Required before a production failover

1. Independent, authenticated, monotonic epoch authority with an
   externally verified high-water and stale-controller rejection.
2. Transactional durable call/ACK/result capture tied to exact
   authenticated client and device, including crash injection.
3. Explicit quarantine and reconciliation of uncertain operations
   without automatic replay or duplicate side effects.
4. Recovery/restore evidence from an independent custody domain.
5. Separate, owner-approved standby identity and relay mTLS credentials;
   no copying of production controller keys.
6. Controlled, reversible cutover with proven single-leader fencing.

Separately, x1 Ubuntu package recovery remains P0 HOLD pending
offline root+EFI imaging and a boot-tested restore. Fleet node Task
agent HTTP 401s remain a separate Auto-Assist issue #501.

Tracking: https://github.com/scottjoyner/auto-router/pull/125
Recovery: https://github.com/scottjoyner/auto-router/issues/124
Agent auth: https://github.com/scottjoyner/auto-assist/issues/501
