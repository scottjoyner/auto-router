# SGNC → LFM3: read-only proof boundary, November 7, 2026

## Verified local source interfaces (code inspection only)

Native SensorGraft / SGNC research source:
- Local branch: sensorgraft-sgnc-relying-party-20261102, commit b0454e7.
- Native Rust src/sgnc_integrity.rs: MacWitness is a domain-separated
  HMAC-SHA256 shared-key witness over count, head digest, generation and key ID.
  Its own source explicitly says this is NOT a public-key signature or durable
  anti-rollback authority. verify_mac requires pinned independent 32-byte
  verifier key and minimum generation.
- Python research/m61_external_epoch_gate.py: separate operator Ed25519 epoch
  certificate under domain SENSORGRAFT:M61:EXTERNAL:RESOURCE:EPOCH:V1,
  independently pinned operator key, an operator policy, exact native SQLite
  resource fence state, and protected filesystem ancestry/identity checks.
  This is NOT the LFM3 research-evaluation signing domain.

The LFM3 research evaluator added in commit 238feac uses a DIFFERENT signing
domain LFM3-RESEARCH-EVAL-ATTESTATION-v1 and attests candidate/baseline report
digests, trial/run/checkpoint identities, workload, key ID and epoch. A positive
LFM3 signature MUST NOT be interpreted as a native SGNC receipt, native M61
external epoch certificate, verifier-controlled HMAC witness, or evidence of
independent custody.

## Concrete blocker confirmed in native M61 code

The local research M61 check_physical() explicitly reports:
- signed_anchor_nonrestorable_in_hardware = False;
- resource_enforces_external_epoch_at_native_commit = False;
- independent_key_and_operator_process_custody = False;
- physically_nonrestorable_anchor_proven = False;
- native_commit_fencing_proven = False;
- production_authorized = False; publication_ready = False.

This is a CODE inspection, not a claim that a production operator certificate,
public key, protected resource, or private signing material exists on x1-370.

## Smallest safe next gates

1. Select the exact native SGNC release and verify its current Rust/Ed25519
   and resource-epoch schema using signed source and exact-head CI.
2. Use an independent relying-party process and a pre-authorized operator key
   registry. Never derive or automatically pin trust keys from untrusted data.
3. Read signed native receipts under strictly read-only, fixed paths with
   explicit custody. Do not copy any keys/receipts into hosted free-model tasks.
4. Obtain an external, independently nonrestorable epoch high-water and a
   durable check at native commit, not merely an in-memory caller epoch.
5. Only after native signature, resource identity, high-water, custody and
   exact-head CI independently pass can an LFM3 context adapter CONSIDER
   admitting verified public/releasable trace records. This does not
   automatically authorize routing, training or model weight updates.

**State:** Native SGNC proof integration HOLD; local Ed25519 research verifier
PASS at synthetic test scope; production routing and signer authority HOLD.
No production, credential, model-weight or native resource mutations were made.
