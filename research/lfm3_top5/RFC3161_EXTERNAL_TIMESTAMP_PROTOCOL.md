# RFC 3161 external timestamp verification — optional read-only witness utility

**Research only.** The existing LFM3 top5 pure research package deliberately forbids `subprocess`, network and production imports. This optional external-witness CLI therefore lives separately at `scripts/lfm3_rfc3161_anchor_verify.py`. It invokes only local `/usr/bin/openssl` with fixed argument arrays, bounded timeouts, temporary 0700 files and no shell, external connection, signing keys, production paths or model inference. It preserves the frozen #152 scorer, #153 stability contract, #156 three-role signatures and #158 primary reconciliation unchanged.

## End-to-end immutable binding

1. An **independent** case author and gold reviewer prepare and separately adjudicate a new, confidential fictional/sanitized dataset under the *preexisting* `lfm3.prospective.cases.v1` corpus contract; freeze before any model call. Do not use the developer-authored 36 known cases for this.
2. The dataset's canonical digest must be the same `dataset_sha256` committed by `lfm3.custody.dataset-commitment.v1`, alongside pinned prereg SHA-256 `38aeb6eba605cfff44f6d46e15b0afe23b9fbe0b405561ae7848e1124d645696` and checkpoint SHA-256 `43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452`. The author and gold reviewer sign the role-separated commitment exactly as documented by #156.
3. The **independent witness** constructs a real RFC 3161 SHA-256 timestamp query with unpredictable nonce and certificate requested. The message imprint is `SHA256(canonical(commitment).encode("utf-8"))`, **not** the digest of raw JSON bytes, the dataset alone or a model response. Submit only this digest/query to a separately approved legitimate external timestamp authority (TSA), retaining the original `request.tsq` and returned **DER** `response.tsr`. Do not send dataset text, private identities, keys or host receipts to that TSA.
4. The witness signs the existing three-role bundle with `claimed_external_anchor_reference="sha256:" + SHA256(raw_response_tsr_bytes).hexdigest()`. The verifier requires this exact token hash to be bound in the witness's signature and verifies author and reviewer role signatures, dataset/prereg/model identity, all three non-reused role keys, and the canonical commitment.
5. The **operator must independently obtain and authenticate** the TSA CA trust root, signer identity, certificate chain, and an external SHA-256 fingerprint of the exact trusted CA PEM. Do not trust a CA fingerprint merely because it appears in the same unverified bundle, environment, Git commit or ChatGPT response. Separately check issuer authenticity, certificate revocation, trusted time source, timestamp chronology **before the first model call**, request nonce origin and retention of a durable third-party audit receipt.
6. Run the offline verifier, which confirms strict DER wrappers, query SHA-256 imprint and nonce presence, original TS query/response nonce match, RFC 3161 signature/certificate chain and witness binding. An extra PEM file holds untrusted intermediate/signer certificates; the response itself may already embed the signer cert. **No output authorizes release.**

## CLI invocation on the authorized review host

```bash
PYTHONPATH=. python3 -m scripts.lfm3_rfc3161_anchor_verify \
  --dataset /approved/private/independent-cases.json \
  --bundle /approved/private/three-role-handoff.json \
  --key-roster /approved/private/independently-authenticated-public-keys.json \
  --timestamp-query /approved/private/request.tsq \
  --timestamp-response /approved/private/response.tsr \
  --ca-pem /approved/private/independently-trusted-tsa-root.pem \
  --signer-chain-pem /approved/private/untrusted-tsa-signer.pem \
  --ca-pin-sha256 ORIGINAL_OUT_OF_BAND_AUTHENTICATED_ROOT_PEM_SHA256
```

**Owner-private file requirement (RFC3161 CLI intake hardening):** All seven
input paths must be absolute, canonical paths with no `.` or `..` and no
symlinked ancestor or final component. The immediate parent directories must
be owned by the custodian and mode **0700**, including the copies of the
*public* CA and signer-chain PEM files. Input regular files must be owned by
the custodian, single-linked, nonempty, owner-readable mode **0600 or 0400**
(no group/other access or execute permissions); arbitrary symlinks, hard
links, FIFOs and group/world-readable inputs are rejected before the verifier
examines the timestamp. Original trusted CA provenance remains a separate
human/out-of-band decision: copying a CA into a private folder does **not**
make it trusted. The bounded input caps are dataset **2 MiB**, three-role
bundle **64 KiB**, public roster **64 KiB**, each DER timestamp query/response
**128 KiB**, and each CA/signer-chain PEM **256 KiB**. Any unsafe or overlarge
file returns HOLD without dumping the original private source, paths, notes,
gold, token, or signer identities. No source of independent trust is created
by this filesystem change.

The external-witness scripts use only local fixed `/usr/bin/openssl` list
commands (`ts -query` and `ts -verify`) with bounded subprocess timeouts,
`stdin=DEVNULL` and no `shell=True`. This protects against a shell command
injection at that subprocess boundary, but the source file still requires
normal code review and no external issuer or online revocation check is
performed. The bounded private-file input check rejects a malicious
symlinked parent without trusting `Path.resolve()` to authenticate custody.

Exit 0 / `PASS_OFFLINE_TIMESTAMP_CRYPTO_ONLY` means the signer/trust-chain and nonce/imprint satisfy the **supplied** trust inputs, nothing more. Exit 2 / HOLD means incomplete, mismatched or malformed evidence. Report only canonical digests, trust-pin and Boolean gates; do not print raw narratives, response text, token contents, private keys, issuer secrets or machine identifiers.

## Provenance and authority limitations

- An untrusted operator could create their own CA, signer and three role-keys, produce a perfectly valid token, then falsify its external pedigree. The synthetic tests do exactly this **using only ephemeral local keys** to verify the cryptographic software; therefore the proof is *not* independent identity, custody or chronology evidence.
- Passing the cryptographic check cannot show the timestamp was acquired before the model's first request without an **independently authenticated timestamp issuer and independently witnessed execution start**. No trusted third-party issuance/revocation proof, actual registered identities, prior signed case corpus or production admission has been obtained.
- The utility refuses indefinite-length/trailing DER wrappers, absent nonce, mismatch of canonical commitment imprint, token substitution, changed source/oracle, missing role signature, reused keys, wrong CA fingerprint and signer-chain failures. It cannot prove that the captured TSA clock is trustworthy without independent approved TSA policy, nor does it perform live CRL/OCSP checks (no network). Those are explicit outside dependencies, not implicit successes.
- Every return path sets `issuer_independently_authenticated=false`, `nonce_origin_verified=false`, `pre_inference_chronology_verified=false`, `trusted_timestamp_source_verified=false`, `external_custody_verified=false`, `model_value_verified=false`, `production_authorized=false`. External custody release remains **HOLD** under issue #157 regardless of synthetic or hosted unit tests.

**Actual next gate:** an external human/org independently registers the author, reviewer, witness and TSA identities, obtains a genuinely timestamped commitment *before model inference*, verifies the original issuance log and revocation status out of band, and deposits the sealed original receipts under authorized custody. Only then can operators request a new case run under #158, and even that run grants **no** production rights.
