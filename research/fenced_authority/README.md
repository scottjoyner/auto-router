# PostgreSQL single-primary execution fencing — research, October 19, 2026

**NOT DEPLOYED. NOT AUTHORIZED FOR PRODUCTION USE. GLM/Lenovo fallback HOLD.**

This is a stacked review-only experiment following Auto-Router
[owner fence #72](https://github.com/scottjoyner/auto-router/pull/72),
[durable attempt ledger #73](https://github.com/scottjoyner/auto-router/pull/73),
[signed terminal custody #74](https://github.com/scottjoyner/auto-router/pull/74)
and AssistX
[owner-source #413](https://github.com/scottjoyner/auto-assist/pull/413).

## Why

#74 demonstrates that two independent SQLite ledgers *both* accept the
same correctly signed terminal receipt. This prototype tests one shared
transactional SQL primary rather than another copy of local receipt state.

This experiment is **a mechanism**, not a source of permission. Existing
AssistX admission, authenticated human owner, signed active claim, live
revocation and projection authority must be resolved by a separately
trusted broker BEFORE any request may call SQL functions. PostgreSQL
function parameters alone are not authenticated identities or proof
of authorized execution.

## Contract and safety invariant

Source: `research/fenced_authority/schema.sql`.

- Per-scope row contains **one active database epoch** and canonical
  hashed owner/claim/projection generation. Grant fails if an active
  epoch exists. After explicit correct-epoch revocation, the next
  grant increments the durable epoch.
- Shared scope row is locked with `SELECT ... FOR UPDATE` before a
  signed attempt is reserved or a terminal receipt is committed.
  Active/revoked/expired/wrong owner/wrong claim/wrong generation/wrong
  epoch requests are denied.
- Per-token JTI reservation is shared in the same transaction and
  uniquely bound to owner, claim, **epoch**, projection generation,
  expiry and attempt ceiling. A still-unspent old JTI cannot carry
  authority into a replacement epoch.
- Every request ID is unique; each signed JTI/attempt pair is unique.
  Each terminal receipt digest is also globally unique in this
  database. An identical terminal replay returns duplicate; a
  conflicting outcome cannot overwrite a prior terminal outcome.
- Terminal statuses distinguish completed, failed, cancelled, and
  possibly_accepted. Under revocation, *late terminal acceptance*
  fails, even if an earlier worker may physically keep generating.
- PostgreSQL default PUBLIC execute privilege on existing functions is
  explicitly revoked, together with PUBLIC schema/table privileges.
  **Future function safety:** the migration now also changes global default
  function EXECUTE privileges for the *current creating role* (a per-schema
  default REVOKE cannot cancel PostgreSQL's built-in global PUBLIC grant).
  The disposable native harness creates a new research function and confirms
  an untrusted role cannot execute it, then drops the probe. This is not a
  fleet migration policy: every distinct future creating role needs its own
  secure defaults and independent ACL readback.
  No operating broker role or credential is created by this research
  migration. Production would require explicit broker-only grants.

**Critical distinction:** rejecting a late receipt is NOT fencing a
model-side effect already in progress. To enforce safe takeover,
resource/model workers must themselves check epochs and revoke/stop
old work; otherwise dual execution can still happen during a partition.

## Native physical test — PASS, single shared primary only

Harness: `python3 research/fenced_authority/qualify_pg.py --run`.

The default is PLAN_ONLY. A run requires a locally preinstalled
`postgres:17-alpine` image, uses a disposable, uniquely named
container with `--network none`, no published ports, no bind mounts,
and caps (2 CPUs, 1 GiB memory, 128 PIDs). PostgreSQL local Unix
socket is accessed only by multiple separate `docker exec ... psql`
client processes from x1-370. All client values are synthetic 64-hex
digests; no fleet user/Task or model traffic is touched. On success
and failure, the exact disposable container is forcibly removed.

Evidence:
`research/fenced_authority/evidence/native_single_primary_20261019.json`
(image digest, schema digest, bounded test results; payload-free).

Observed result:
- 6 competing SQL client processes, shared signed attempt budget 2:
  **2 admitted, 4 denied**.
- 2 simultaneous competing grants for a previously absent scope:
  **one winner, one denied**.
- Correct grant starts epoch 1. Active competing grant denied.
  Revocation at epoch 1 rejects late reserve and late terminal;
  replacement grant obtains epoch 2.
- Old epoch 1 signed JTI with **unused** allowance cannot be reused at
  epoch 2, even with the same owner and claim.
- Exact duplicate terminal is idempotent, conflicting outcome rejected,
  and duplicate receipt digest across requests rejected.
- PostgreSQL role without broker privileges cannot execute grant.
- Private hash-only evidence is captured. Disposable container removed.

Nine hermetic structural tests also pass without Docker.

This verifies single-primary row locking, uniqueness and atomic updates
across *different local client processes*. It does NOT verify access
from two physical router hosts, a replication failover, WAL durability
through power loss, rollback-resistant checkpoints, election under
network partition, a remote signer, live authenticated human owners,
or a real loaded model.

## Nonnegotiable next gates before any deployment

1. Implement a genuinely trusted production-facing broker: validate
   issuer/signature/revocation/owner, canonical AssistX claim status,
   projection generation, request and capability policy before any SQL
   call. Privilege only the broker; untrusted callers and model workers
   must have no DB credentials or direct SQL execution permission.
2. Bind the signer and actual worker-side terminal stream to the
   current epoch and source-backed model execution. Verify signed
   receipts from a separately controlled key (not a router-held
   arbitrary success token). Preserve possibly_accepted uncertainty.
3. Use one primary with proven synchronous durability, replication,
   explicit RPO/RTO, isolated key custody, WAL backup, and **independent
   monotonic checkpoint/high-water** to reject restored old snapshots.
4. Physically race x1 and xwing using two separately authenticated test
   principals, partition one, revoke and replace its epoch; prove the
   old worker stops model-side effects, not merely that SQL rejects its
   late receipt.
5. Prove server cancellation, cross-owner isolation, stale leases,
   restart replay, disk-full fail-closed, role privilege and key
   rotation. Then controlled rollback and 72-hour production-like
   serving soak with measured tail latency and task compatibility.
6. Qualify fallback *capabilities*: a small Lenovo LFM CPU model is not
   an equivalent replacement for GLM 321B. Preserve an explicit
   unavailable outcome when no admitted capable provider exists.

No PostgreSQL/Neo4j production schema, gateway config, live routing,
model load, node power, credentials, or existing fleet services were
changed.

## Regression evidence and CI caveat

Nine static contract tests plus adjacent owner/attempt/receipt/fence
tests: **119 passed**. The full non-integration suite against this
isolated worktree: **505 passed, 9 skipped, 1 failed**.
The failure is the pre-existing live fleet registry drift test:
x1-370 advertised `toolcall-v5-3b-combined-r2` but did not serve
it during two catalog samples. This was reproduced in earlier work
before this SQL experiment; the gate has not been disabled or weakened.
Static tests are hermetic; only the explicitly operator-triggered
`qualify_pg.py --run` starts a disposable PostgreSQL instance.

## Physical x1/xwing qualification — October 19

See PHYSICAL-X1-XWING-20261019.md, pgwire_client.py,
qualify_twohost.py, the physical_x1_xwing_20261019.json evidence
and test_pg_twohost_fence_research.py. Two actual physical clients
competed over loopback-bound ports and temporary pinned SSH tunneling.
The bridge was NOT internal-only (Docker internal networking prevented
host loopback publication); no external port was published and all
values were synthetic. All ephemeral resources were removed. These
tests do not prove independent owner custody, replicated consensus,
or stopping an old-epoch model worker.
