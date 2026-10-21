# Fleet publisher identity envelope — offline research (2026-10-21)

## Problem and physical motivating evidence

On the actual Joyner R9700 host, two reporting-related processes are active: a 30-second model catalog reporter and a 60-second unadmitted GPU-presence heartbeat. Both target the same Auto-Router hostname. The router currently accepts unsigned `POST /api/fleet/node-report` from a caller asserting `hostname=joyner`. A 13-sample/60-second witness reproduced two alternating report formats and catalog variants; see [lms issue #50](https://github.com/scottjoyner/lms/issues/50).

[Draft PR #79](https://github.com/scottjoyner/auto-router/pull/79) isolates the known *unadmitted* heartbeat format, but a payload-shape classifier does **not** authenticate a publisher. The two reports must have independently pinned signing authorities and cannot elevate model inventory into actual GPU-resident execution.

## Candidate v1 Ed25519 contract

`src/auto_router/fleet_publisher_envelope.py` is deliberately **not wired to the router**. It has no FastAPI endpoint, no signer utility, no generated live secrets, no network access and no production state writes.

Each detached, Ed25519-signed JSON envelope contains these **exact** fields:

```json
{
  "schema": "fleet-publisher-envelope.v1",
  "publisher_id": "joyner-model-catalog",
  "node_id": "joyner",
  "role": "model_catalog",
  "key_id": "joyner-catalog-key-v1",
  "sequence": 1,
  "issued_at_ms": 1792585000000,
  "expires_at_ms": 1792585030000,
  "payload": {
    "library": ["joyner-ling", "joyner-toolcall"],
    "loaded": []
  },
  "signature": "<detached URL-safe Ed25519 signature, base64url without padding>"
}
```

**Never treat this illustrative document as a valid signed receipt**; the signature is absent. Only individually operator-pinned **public verification keys** may be used in `TrustedPublisher`.

The separate GPU heartbeat role is `gpu_presence`; its exact payload contains only boolean `gpu_canary_guarded` and `dispatch_admitted=false`. It cannot claim any model alias, model loading or routing capacity.

The canonical signed message has domain prefix `auto-router:fleet-publisher:v1\\0` followed by sorted compact ASCII JSON of all envelope fields **except** the detached signature. The envelope and known aliases are strictly bounded. The verifier requires the pinned key, role, node, key ID, bounded lease, exact payload shape, valid Ed25519 signature, and an increasing per-publisher sequence. Invalid signatures cannot advance counters. Key IDs cannot be shared between reporters.

The result is a **diagnostic** `PublisherDiagnosticReceipt` with `signed_publisher_identity_verified=true`. All of `publisher_custody_verified`, `replay_protection_durable`, `loaded_model_attested`, `gpu_residency_verified` and `subject_owner_bound` remain **false**; `production_admission=NO_GO`.

### Offline verification

```bash
cd /home/scott/git/wt-auto-router-publisher-auth-20261021
PYTHONPATH=src /home/scott/git/auto-router/.venv/bin/python -m pytest -q tests/test_fleet_publisher_envelope.py
/home/scott/git/auto-router/.venv/bin/ruff check src/auto_router/fleet_publisher_envelope.py tests/test_fleet_publisher_envelope.py
```

Tests generate **ephemeral private keys in their own Python process**. They cover forged and swapped signatures, key reuse, role confusion, unsigned legacy format, timestamp skew/expiry, rollback/duplicate sequence, concurrent duplicates, out-of-scope paths/aliases and attempts to claim GPU residency. No real private key is created or installed for Joyner.

## Unresolved release and safety gates

1. **Key custody:** Set up two separate real keypairs with independent operator control, provisioning and rotation/revocation; pin the exact public keys to verified host/process identities.
2. **Durable replay/fencing:** `InMemoryReplayFence` is deliberately volatile and reaccepts old data on restart. Production requires an independently controlled, rollback-resistant sequence high-water witness with atomic updates and revocation handling.
3. **Host/process evidence:** Prove the exact protected `joyner` heartbeat process executable and its process identity without exposing its API keys. Establish the proper identity claims for both publishers.
4. **Transport admission:** Independently review and test any future authenticated endpoint; do not use the legacy unsigned node report as a trusted source or allow a signed catalog to override `loaded` claims.
5. **Actual execution owner binding:** Authenticated subjects/tenants, model execution receipts, GPU residency, stream terminal receipts, cancellation and cross-user separation remain blocked under [Auto-Router issue #37](https://github.com/scottjoyner/auto-router/issues/37).
6. **Hardware/model:** No 64 GiB RAM upgrade or real R9700 IQ3_S benchmark is verified. The four existing inference servers must remain running until an independently approved maintenance window.

This document does **not** authorize a production router deployment, a new GPU context, an IQ3_S load, or the conclusion that any particular model is optimal.


## Offline SQLite high-water research (October 21 continuation)

The verifier can now be constructed with an **explicitly opt-in**
`LocalSQLiteReplayFence(path)`. This is a local research backend, **not**
externally anchored replay authority, and it is **not** used by a live router
endpoint or by the default verifier. It accepts only an absolute local SQLite
path and stores per-publisher monotonic sequence values with
`BEGIN IMMEDIATE`, SQLite conflict-conditional update, and
`PRAGMA synchronous=FULL`. It explicitly closes SQLite handles after each
operation, rejects duplicate/older sequence numbers across verifier restarts,
and serializes concurrent writers sharing the same file. It does not store
private keys, prompts or model weights.

**Security distinction established by an executable negative test:**

| Scenario | In-memory fence | Local SQLite research fence | Independently controlled durable authority |
|---|---|---|---|
| Duplicate sequence in one verifier | Rejects | Rejects | Required |
| New verifier / ordinary process restart | Loses state | Rejects old sequence | Required |
| Concurrent verifier instances | No shared process state | Atomic local-file enforcement | Required |
| Restore old on-disk snapshot, then restart | Loses state | **Old sequence can be accepted again** | Must reject |
| Remote custody / revocation / operator rollback | Not proven | Not proven | **Not implemented** |
| Production model admission | **NO_GO** | **NO_GO** | Separate source and execution gates still required |

The test deliberately snapshots the database after sequence 1, accepts
sequence 2, restores the older image, starts a fresh verifier and confirms
sequence 2 is accepted again. The resulting receipt continues to say
`replay_protection_durable=false`, `publisher_custody_verified=false`,
`gpu_residency_verified=false` and `production_admission=NO_GO`. A
successful local SQLite durability test must never be described as
rollback-resistant security.

Reproduce **only in disposable test directories**:

```bash
PYTHONPATH=src /home/scott/git/auto-router/.venv/bin/python -m pytest -q \
  tests/test_fleet_publisher_envelope.py \
  tests/test_fleet_publisher_sqlite_replay.py
```

Independent next gate: define a non-self-issued checkpoint (external witness
with separate key custody, atomic compare-and-swap, latest accepted high-water,
revocation, signed proof, and a negative on-disk rollback test). The production
router must fail closed if that authority is unavailable. Physical Joyner
publisher keys, reporter code deployment, API integration and GPU residency
remain unverified.

## Signed independent checkpoint comparison — offline research (October 21)

The **new** `fleet_publisher_checkpoint.py` module verifies a second,
domain-separated Ed25519 signature from a separately pinned observer key. It
uses a fixed `fleet-independent-checkpoint.v1` envelope containing the
observer/key identity, monotonic checkpoint generation, short issuance/expiry,
an **exact** map of all expected publisher sequence high-water marks, a
deny-only revocation list and a detached Ed25519 signature.

The caller must separately supply the **latest checkpoint generation** from
a trustworthy, independent authority. The signed checkpoint is validated
against that pinned generation before comparing it with a **read-only
transactional snapshot** of the local research SQLite database. Unknown
publishers, removed counters, changed values, invalid signatures, expiry,
wrong observer identities, revoked publishers, missing/invalid databases and
an older checkpoint generation are rejected.

The synthetic test fixture uses an ephemeral `lenovo-independent-observer`
key separate from both ephemeral Joyner publisher keys. **This is only the
observer's illustrative name, not proof that Lenovo currently controls such
a signing key or runs a durable checkpoint service.** No real observer key,
publisher private key, network endpoint, authoritative generation database
or external checkpoint publication has been created.

**Negative rollback qualification:**

| Offline test | Result | Trust implication |
|---|---|---|
| Local SQLite rollback after checkpoint for sequence 2 | **REJECTED** | Works only when the independent latest checkpoint stays available and current |
| Replayed valid older signed checkpoint while external generation remains at 2 | **REJECTED** | Correctly compares against independently supplied generation 2 |
| Local database **and** supplied latest-generation anchor both rolled back to 1 | **ACCEPTED IN SYNTHETIC TEST** | Independent rollback resistance is **not established** |
| Changed signed high-water/revocation or reused publisher key | **REJECTED** | Signature and role separation work with pinned trust roots |
| Checkpoint unavailable, expired, corrupt, or with missing publisher | **REJECTED** | Fail closed rather than fallback to local state |

The assessment always sets `externally_anchored_latest_verified=false`,
`external_authority_custody_verified=false`,
`live_replay_protection_verified=false`, `gpu_residency_verified=false`
and `production_admission=NO_GO`. Local comparison does **not** make the
checkpoint itself recent, unforgeable by its custodians, or physically
independent, and the one-shot read is not an atomic live cross-node
compare-and-swap. Separate publication/fencing, independent key custody,
revocation and authenticated process provenance remain necessary.

Reproduction on the research host (test-scoped disposable SQLite files only):

```bash
PYTHONPATH=src /home/scott/git/auto-router/.venv/bin/python -m pytest -q \
  tests/test_fleet_publisher_checkpoint.py \
  tests/test_fleet_publisher_envelope.py \
  tests/test_fleet_publisher_sqlite_replay.py
```

There is **no production integration**, no live `/api/fleet` publisher
endpoint, and no verified GPU inference. Do not merge this research as a
production-admission authorization.

## Observer-side atomic checkpoint issuance — offline-only research

`fleet_checkpoint_authority_research.py` models a **synthetic, separate observer**. The test operator supplies an Ed25519 private key, an explicit SQLite path, separately pinned publisher public keys, and synthetic bootstrap high-water counters. There are NO live fleet keys, no Lenovo service, no API route, and no production writes.

The offline observer checks signed publisher role/key/node/lease, then atomically advances only that publisher's high-water sequence using SQLite `BEGIN IMMEDIATE` and a compare-and-swap **expected generation**. It signs the new full checkpoint, offers readback of the signed document, rejects stale generations and replays, and can record monotonic publisher revocations. Observer/publisher key reuse and unexpected trust-key replacement are rejected.

**Security boundary:** The observer signing key and its database are under the SAME test operator's control. This is not physical independence, and SQLite durability is not rollback resistance.

| Synthetic scenario | Result |
|---|---|
| Competing updates with the same expected generation | Exactly one accepted |
| Restart with previously accepted publisher sequence | Replay rejected |
| Forged publisher signature / model-vs-GPU role confusion | Rejected |
| Revoked publisher attempts to report | Rejected |
| Roll back local reporter DB while latest external generation remains pinned | Rejected |
| Roll back BOTH observer DB and reporter DB; client derives latest generation from rolled-back observer | OLD SIGNED STATE ACCEPTED — NO_GO |
| Changed observer verification key or deleted database | Rejected |

The module's `latest()` merely reads a stored signed document. Consumers MUST independently verify its signature and compare against an externally controlled latest-generation anchor. Even a successful synthetic comparison returns `production_admission=NO_GO`. The fixture's genesis counters are not evidence of real-world reporter history.

Production still requires independent signing-key custody, trusted genesis, an authenticated observer endpoint, atomic remote CAS, current signed checkpoint readback, negative remote rollback tests, revocation, outage fail-closed behavior, and separately verified owner-bound GPU execution receipts. No remote observer endpoint or physical GPU admission was enabled.

Run against disposable test files only:
`PYTHONPATH=src /home/scott/git/auto-router/.venv/bin/python -m pytest -q tests/test_fleet_checkpoint_authority_research.py`

## Actual off-host disposable checkpoint qualification — October 21, 2026

**Physical machines:** x1-370 control/verification host and Lenovo
(`scott-Lenovo-ideapad-330S-15IKB`) executing an ephemeral observation
process reached through pre-existing strict-host-key SSH. Both SSH
sessions were operated as `scott`; **separate administrative custody is
not proven**.

New fully reproducible source files:

- `scripts/temporary_offhost_observer.py`: streamed as Python source
  on the remote host via SSH stdin. An Ed25519 key is generated in that
  process memory and is never serialized to an on-disk private-key file.
  A SQLite database in a self-cleaning temporary directory stores
  synthetic generation 1 and is advanced with `BEGIN IMMEDIATE` to
  generation 2. Two signed checkpoints and a challenge-bound signature
  are returned through stdout; no daemon/listener is installed.
- `scripts/qualify_offhost_fleet_checkpoint.py`: verifies a fresh
  challenge and signatures, creates a completely separate temporary
  x1-370 SQLite reporter fence, compares both checkpoints with the
  existing `fleet_publisher_checkpoint` verifier, deliberately
  restores the old local database, and requires a **rejection**
  against the retained second checkpoint. It also verifies an older
  signed checkpoint is rejected against the newer pinned generation.
  Every assessment returns `production_admission=NO_GO`.
- `tests/test_offhost_checkpoint_qualification.py`: offline
  synthetic positives and negatives including tampering, invalid
  remote proof, missing/failed/timeout SSH, oversized/unparseable
  output, expiration, restrictive command flags, and fail-closed
  behavior.

Run from the **isolated** research checkout on x1-370 only:

```bash
cd /home/scott/git/wt-auto-router-publisher-auth-20261021
PYTHONPATH=src /home/scott/git/auto-router/.venv/bin/python \
  scripts/qualify_offhost_fleet_checkpoint.py --host lenovo
PYTHONPATH=src /home/scott/git/auto-router/.venv/bin/python -m pytest -q \
  tests/test_offhost_checkpoint_qualification.py
```

This **does not** use the production router POST endpoint, alter Joyner
LLM services, distribute keys to fleet nodes, or persist observer signing
keys. The remote Linux filesystem is touched only within a temporary
directory, deleted at process exit. No existing Lenovo service is
changed.

**Evidence and limits**: The first live Lenovo→x1-370 run passed
challenge-bound signed transcript verification, generation progression,
local rollback rejection with the retained signed checkpoint, and stale
checkpoint rejection. The observer public key was **self-presented
inside the same SSH-controlled test session**; it was not verified
against an independently held trust root, HSM or different
administrator. The checkpoint was generated from synthetic counters;
no physical Joyner process provenance or live accepted-sequence
history was attested. If an attacker controls both checkpoint
generation and the supposed latest anchor, the tests still show that
rollback can evade detection. A physical two-host test does not
establish a tamper-resistant replay authority.

Before production admission: separate observer key and service
custody, independently anchored current-generation readback,
remote atomic CAS and signer attestation, revocation and outage
denial evidence, protected host/process identity, and owner-bound
execution receipts remain mandatory. **This cannot promote IQ3_S.**