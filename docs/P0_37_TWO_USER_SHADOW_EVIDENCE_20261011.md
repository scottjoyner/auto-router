# P0 #37 — two-user shadow execution receipts: evidence and rollout stop (2026-10-11)

**Status:** DRAFT / TEST-ONLY; **NO PRODUCTION RELEASE GATE CROSSED**.

Source issue: [#37](https://github.com/scottjoyner/auto-router/issues/37).
Draft dependency chain: [#35](https://github.com/scottjoyner/auto-router/pull/35)
→ [#36](https://github.com/scottjoyner/auto-router/pull/36)
→ [#42](https://github.com/scottjoyner/auto-router/pull/42)
→ [#45](https://github.com/scottjoyner/auto-router/pull/45).
This document records **observed checks**, not a deployment authorization.

## Exact-head local verification

Observed on **x1-370**, 2026-10-11, in a separate checkout and disposable
Python virtual environment under `/tmp/auto-router-p0-37-*`.

| Evidence | Result |
| --- | --- |
| Exact tested SHA | `fa759120e374597f7b85dc8b2b0a572fb537642b` |
| Focused stream lifecycle + 4 shadow modules | **36 passed**, 0.30 s |
| Ruff on all 4 shadow test modules | **all checks passed** |
| Full suite, known registry-drift test excluded | **431 passed, 11 skipped, 1 deselected**, 9.32 s |
| Unfiltered full suite (-x) | **1 failed, 300 passed, 11 skipped**; stopped at known registry-drift check, 21.75 s |
| Exact-head GitHub Actions workflow | None returned when checked |
| Production route / model / outbox / custody change | **None** |

Reproduction in an isolated checkout with project development dependencies installed:

```sh
python -m pip install -e '.[dev]'
ruff check tests/test_two_user_shadow_receipts.py \
  tests/test_two_user_shadow_adversarial.py \
  tests/test_two_user_shadow_sqlite_replay.py \
  tests/test_two_user_shadow_offline_replay.py
python -m pytest -q tests/test_stream_lifecycle.py \
  tests/test_two_user_shadow_receipts.py \
  tests/test_two_user_shadow_adversarial.py \
  tests/test_two_user_shadow_sqlite_replay.py \
  tests/test_two_user_shadow_offline_replay.py
python -m pytest -q --disable-warnings -k 'not test_registry_matches_live_hosts'
```

**Caveat:** the last command deliberately deselects one real, failing fleet
inventory check. It is not equivalent to a clean, unfiltered CI run.
Do not mark the full suite green or remove the drift gate.

## Live local residency observation (not execution attestation)

A **read-only GET** to x1-370 LM Studio's local
`http://127.0.0.1:1234/api/v1/models` returned HTTP 200 and 26 catalog entries.
Every entry reported an empty `loaded_instances` list; 0 entries reported
`loaded: true`, and `state` was absent on that API version. This is **catalog
observation only**, not a provider process witness or proof that no other
runtime could be serving elsewhere. No model was loaded or queried for
inference. The configured-provider drift check separately failed for:
`lmstudio-x1-370: qwen3.5-0.8b-claude-4.6-opus-reasoning-distilled`.

**Consequence:** the x1-370 physical two-user LM Studio execution gate is
**BLOCKED** pending a positively identified, resident model and independently
observed provider execution. Do not use AVAILABLE catalog membership as LOADED,
or LOADED as proof of a specific request actually executing.

## What the additional isolated tests prove

- Two separately generated fixture Ed25519 principals (Alice and Bob) have
  distinct authenticated identities, server-minted roots and attempts.
  Maliciously shared client trace IDs cannot cross readback boundaries.
- The real stream-lifecycle parser distinguishes terminal DONE / Responses
  completion from incomplete EOF, provider failure, transport exceptions, and
  client cancellation. Neither SSE completion nor forged metadata is model
  execution attestation or end-client delivery.
- Disposable SQLite receipts enforce immutable attempts, same-attempt
  idempotence, conflicting-replay rollback, subject/tenant read isolation and
  independent concurrent writes.
- Reopening SQLite retains records; recreating the disposable gateway signer
  requires fresh login and rejects old delegations. Lost retry-root authority
  fails closed; the fixture does **not** prove production key recovery.
- The disposable offline spool authenticates/captures the request **before**
  delegation expiry, seals its **payload-free** receipt with an independent
  spool signer, and permits delayed delivery after expiry only through the
  trusted spool-public-key verifier. Idempotent replay, tampering, foreign
  signer, and conflicting replays are checked. Stale client credentials
  **never** authorize a new capture.

All fixture payloads keep `provider_execution_attested=false` and
`response_delivered=unknown`. A signed spool event proves only that the
**synthetic spool** signed its receipt; it does not attest an actual LM Studio
process. The fixture stores temporary **unencrypted** SQLite data; no approved
production encryption, identity, revocation, key custody, NAS store, or
producer-level permissions were exercised.

## Smallest remaining release-evidence gates

| Gate | Current state | Required independent evidence |
| --- | --- | --- |
| Real A/B identity and authorized model | **OPEN** | Two separately authenticated human subjects; trusted gateway delegation checked at router boundary |
| Reliable residency | **BLOCKED on x1-370 snapshot** | Explicit loaded runtime instance and matching model/host from fresh authoritative observer; no autoload |
| Provider execution witness | **OPEN** | Independent process/model/attempt correlation; signed/secured observer separate from caller metadata and configured route |
| Stream terminal and response delivery | **PARTIAL synthetic** | Live terminal/cancel/incomplete outcomes; delivered-to-client separately verified or UNKNOWN |
| Consumer readback/custody | **OPEN** | Approved encrypted trace sink, tenant + subject + producer + node scoped auth for reads/subscriptions |
| Durable offline replay | **PARTIAL synthetic** | Approved spool and keys, restart/outage and revocation policy, custody reconciliation, no stale delegation trust |
| Real two-user LM Studio / llama.cpp | **OPEN** | Bounded authorized canary, same resident instance, overlapping A/B, trace collisions, retry, offline, second node |
| Capacity/performance and iPhone | **OPEN** | Trace bytes + p50/p95/p99, head SHA, physical iPhone and gateway results, no promotion without their separate checks |

### Operator stop conditions

Keep PRs #35, #36, #42 and #45 **draft-only** for this combined claim.
No automatic model loading, change to `secure_live`, route selection,
production outbox/trace databases, AssistX custody, authentication middleware,
or iPhone release gate. No merge/deploy or public assertion of unified per-user
execution history based on this simulation. A loaded model alone is
insufficient: require independently correlated provider attempt evidence,
real two-user authentication, and production custody before reconsidering.

## Further evidence: independent synthetic observer and residency preflight

A new isolated synthetic provider-observer test module uses a separate
disposable Ed25519 signing authority from the gateway. It correlates
three signed fixture events (resident, attempt_started, attempt_terminal)
by server-minted root/attempt IDs, model, configured node and fixture
runtime instance. Ten tests cover wrong signer, tampering, owner/attempt
collision, model/node mismatch, missing and duplicated events, inconsistent
instance identities, stale timestamps and incomplete stream outcomes.
Even a complete signed fixture sequence reports synthetic correlation ONLY;
provider_execution_attested and physical_provider_proof remain false and
response_delivered stays unknown. No real provider attempt was observed.

A standalone, opt-in read-only diagnostic was also added:
scripts/p0_37_provider_residency_preflight.py. Its default config-only
mode performs zero network requests; explicit --probe performs only bounded
GET inventory probes against validated private provider URLs.
The standalone diagnostic groups by provider service AND physical node.
It does not modify the stricter production discovery validator, whose
current duplicate-node-id check aborts on xwing because the registry
defines multiple listeners on the same hardware. Independent offline
preflight tests: 10 passed.

Actual bounded inventory observations on x1-370 on 2026-10-11:
* 14 LM Studio services, 10 physical nodes; 2 authoritative services,
  3 non-authoritative, 9 unreachable in the bounded scan.
* Beelink LM Studio: 11 observed catalog entries, one native resident model,
  liquid/lfm2.5-1.2b. The loaded instance has config and id fields.
  Two separate GETs returned the same instance identity, which is NOT an
  attested provider request, execution event or stable-persistence guarantee.
* Optiplex LM Studio: authoritative native inventory, 25 catalog entries,
  zero resident models.
* Destroyer K2, Joyner, and Lenovo LM Studio: non-authoritative inventory,
  residency UNKNOWN, not zero.
* x1-370 local LM Studio loopback endpoint: HTTP 200 and 26 catalog entries
  with zero loaded instances; its configured fleet endpoint was unreachable
  in the separate bounded probe. These are different endpoints and do not
  imply the physical host is offline.

No inference request was sent. No model autoload, route mutation, producer
storage, real user login, or change to production authentication occurred.
Beelink is an INVENTORY candidate only, not an approved inference canary.
Any physical model test requires operator approval, capacity assessment,
real separately authenticated humans, independently witnessed request-level
provider execution, and custody-approved tenant+subject+producer+node reads.

### Reproducible new evidence

Exact tested SHA: c10d0b8a482f976b233644657343d09537871838
Ruff on all relevant new modules/scripts: PASS.
Isolated synthetic provider-observer tests: 10 passed.
Isolated static/fake-probe tests: 10 passed.
Broader suite with the known live registry drift check deselected:
451 passed, 11 skipped, 1 deselected, 4.49 seconds.
The unfiltered full suite remains NOT GREEN due to the separate x1-370
configured alias drift. Do not interpret deselection as resolving that failure.

### Smallest remaining evidence-backed physical gate

Verify Beelink's existing resident instance and NAS capacity in a fresh read.
Prove two real separately authenticated principals and signed gateway delegation
to the router. Add real provider-process event correlation for each minted
attempt, independent of SSE completion, configured provider ID, and client
metadata. Approve encrypted trace custody and actor+producer+node readback.
Only after those gates permit a bounded two-user overlapping request canary
on the same instance, with colliding client trace IDs, incomplete stream,
retry and reconnect. Perform a second-node llama.cpp and physical-device
acceptance separately. Until then keep the entire PR chain DRAFT-ONLY.

## Physical canary capacity veto — Beelink

The Beelink inventory candidate has now been **rejected for physical
inference at the observed time**, based on a read-only SSH host inspection
and a deterministic host-pressure evaluator. See
[Beelink NO_GO evidence](P0_37_BEELINK_HOST_PRESSURE_NO_GO_20261011.md).

The new explicit, opt-in `scripts/p0_37_host_pressure_preflight.py`
only reads kernel pressure and filesystem counters; it cannot grant
physical inference. In an actual Beelink run, it returned exit **2 / NO_GO**:
approximately **3.0 GiB root available**, **99.36% root unprivileged
capacity used**, and **23.64% full I/O stall (avg10)**, against conservative
test thresholds of 10 GiB minimum, <90% used and <5% stall. NAS was mounted.
No inference, model autoload, service changes or storage cleanup occurred.

The companion inventory preflight now explicitly emits
`inventory_candidate`, `host_pressure_checked=false` and
`physical_inference_permitted=false` so model residency cannot be
mistaken for approval to run. **No provider in the snapshot was
physically admitted.**

Exact-head check at `ffd672f25528a6d629556fa4ebecbdf4d32d5279`
on x1-370: host/inventory preflight Ruff passed; **469 passed,
11 skipped, 1 deselected** in 4.06 seconds for the full isolated suite
excluding only the existing live registry-drift test. The unfiltered
suite remains not green. All work is draft-only.
