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
