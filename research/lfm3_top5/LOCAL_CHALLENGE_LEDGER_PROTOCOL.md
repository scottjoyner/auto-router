# LFM3 verifier-controlled single-use nonce ledger

**Research-only. Does NOT establish independent human identity or trusted custody.**
The existing key-control verifier in draft PR #169 mathematically accepted the
same valid challenge/signature repeatedly. This opt-in local ledger can reject a
second use of an *issued* nonce on a verifier-owned filesystem. It does not
authenticate the issuer, individual signer, organization or new case corpus.

## Issue and consume, on the independent verifier machine

Create an existing dedicated directory owned by the verifier and mode 0700,
separate from the signer and inference operator. Keep the three-role public
key roster in an owner-only 0600 or 0400 regular file.

The issue command (arguments shown on separate lines):

    PYTHONPATH=. python3 -m scripts.lfm3_key_challenge_ledger issue \
      --key-roster /verifier/private/roster.json \
      --role author \
      --ledger-dir /verifier/private/ledger \
      --challenge-output /verifier/private/author-unique-challenge.json \
      --payload-output /verifier/private/author-challenge-bytes.bin

After the intended person signs the exact challenge bytes on their **own**
secure machine, they return a 64-byte detached Ed25519 signature, never
their private key. The verifier runs:

    PYTHONPATH=. python3 -m scripts.lfm3_key_challenge_ledger consume \
      --key-roster /verifier/private/roster.json \
      --role author \
      --ledger-dir /verifier/private/ledger \
      --challenge /verifier/private/author-unique-challenge.json \
      --signature /verifier/private/author-detached.sig

Use distinct, independently controlled public keys and new challenges for
reviewer and witness. Identity/organizational independence, genuine consent,
human conflict checks and the independent issuance timeline must be
checked through an **externally trusted channel** and logged separately
from this local software. None has been completed in this research fixture.

## Exact local-only claim

- Issue first validates the complete distinct public-key roster and uses the
  existing OS-random 32-byte nonce builder. It creates two owner-private 0600
  files under no-symlink mode-0700 directories using the existing exclusive
  writer. Only *after both are written* it creates and fsyncs an exclusive
  issue record in the verifier's private ledger directory, named by the
  canonical SHA-256 of the challenge. An unrecorded output cannot be consumed.
- Consume first checks Ed25519 signature bytes and exact role. It then opens
  the independently controlled mode-0700 ledger by pinned no-follow directory
  descriptor and reads the strict owner-private issuance record, matching
  its challenge SHA, role, key ID, and public-key fingerprint.
- Successful consume exclusively creates a spent record with O_EXCL and
  fsyncs the private ledger directory. Concurrent verification attempts
  for the same nonce cannot both succeed. Failed signatures do not spend.
  Existing spent entries cause HOLD even if their contents are corrupted.
- On a failed spent-marker fsync the command returns HOLD and intentionally
  does NOT unlink any potentially created spent marker, preventing a simple
  uncertain-write replay.
- Output file creation, issuance recording and power loss are not an atomic
  multi-file transaction: crashes may leave orphan outputs or uncertain
  marker state. New challenges, manually reviewed by the independent
  verifier, are preferable to resetting or deleting spent state.
- A same-UID attacker, forged verifier-operated directory, deleted ledger,
  rollback of filesystem snapshot, disk corruption or malicious human
  issuer can circumvent this LOCAL control. It does not provide TPM-backed
  monotonicity, externally signed immutable event receipts, a secure
  timestamp, or independent challenge issuance provenance.

## Every trust gate remains held

The local success status is PASS_OFFLINE_LOCAL_ONE_TIME_KEY_CLAIM_ONLY. Its
new field local_one_time_claim_recorded can be true, but the existing
independent_challenge_issuance_verified,
challenge_freshness_and_single_use_verified,
independent_human_identity_verified,
signer_organizational_independence_verified, pre_inference_seal_verified,
permitted_to_infer and production_authorized fields remain false.

This is **only** a local duplicate-use control. It is not a real identity
enrollment, independent challenge issuer, externally sealed hidden corpus,
TSA issuance, GPU attestation, model-quality result or production approval.
All external custody requirements in issue #157 remain HOLD. No signer
private key, live model endpoint, production host, provider route or TSA is
accessed by this code.

Synthetic tests run ephemeral in-memory signers, actual private local ledger
files, concurrent claims, invalid signatures, missing/altered issue records,
unsafe directory permissions, replay, partial issue failure and a simulated
uncertain fsync. A structural PASS is never promoted into external custody.
