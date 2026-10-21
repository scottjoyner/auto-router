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