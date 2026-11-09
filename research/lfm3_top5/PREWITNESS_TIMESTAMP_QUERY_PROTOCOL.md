# LFM3 held-out study: two-phase pre-witness case commitment and RFC3161 request

**Draft research-only.** This closes a sequence gap between #156's complete three-role signed handoff and #159's RFC3161 timestamp verification: the **witness cannot sign the hash of a timestamp response before that response exists**. This proposal adds a standalone author+gold-reviewer verification stage and locally creates the *unsigned RFC3161 nonce-bearing query*. It never creates real human signers, contacts a TSA, asks a model for inference, claims independently registered keys or grants deployment permission.

## Roles and precise order

1. A **genuinely external author** creates previously unseen fictional/sanitized cases; a distinct **gold reviewer** adjudicates the hidden category and E# source marker, before either sees a model answer. The original `lfm3.prospective.cases.v1` requirements from frozen #152 remain unchanged (36–250 cases, six labels and challenge minima). Keep original gold and source notes private. This has **not happened yet** and is still issue #157 HOLD.
2. Independently enroll **three different real human key owners** and authenticate their **Ed25519 public keys** in a separately controlled key roster. The test suite uses only fabricated ephemeral keys and cannot substitute for that enrollment.
3. Author and reviewer separately sign the already frozen `lfm3.custody.dataset-commitment.v1` with `role_payload("author", commitment, {}, "unused-before-timestamp")` and `role_payload("reviewer", commitment, {}, "unused-before-timestamp")`. This is the **same role-domain-separated payload** used by #156; the last argument is ignored for those two roles. No witness signature, anchor reference or release claims belong in this preliminary package.
4. The *prewitness bundle* has exactly:

```json
{
  "schema": "lfm3.custody.prewitness-author-review.v1",
  "commitment": {
    "schema": "lfm3.custody.dataset-commitment.v1",
    "dataset_sha256": "<canonical case dataset digest>",
    "prereg_sha256": "38aeb6eba605cfff44f6d46e15b0afe23b9fbe0b405561ae7848e1124d645696",
    "checkpoint_sha256": "43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452"
  },
  "signatures": {
    "author": {"key_id": "<previously authenticated ID>", "signature_hex": "<Ed25519 signature hex>"},
    "reviewer": {"key_id": "<separately authenticated ID>", "signature_hex": "<Ed25519 signature hex>"}
  }
}
```

5. `prewitness_commitment.inspect_prewitness` validates frozen preregistration identity, existing case balance/schema, canonical full corpus+gold digest, unchanged checkpoint and two **genuine Ed25519 mathematical signatures** under three *distinct* supplied public keys. It rejects extra authority fields and premature witness signatures. Its maximum result is `PASS_OFFLINE_TWO_ROLE_COMMITMENT_CRYPTO_ONLY`—**not independent ownership, case custody, or a release**.
6. Authorized local witness runs `scripts.lfm3_prepare_prewitness_tsq`, which verifies the two-role package again and calls local `/usr/bin/openssl ts -query -sha256 -digest <SHA256(canonical(commitment))> -cert -out ...` with OpenSSL's fresh nonce. It confirms the generated original TSQ DER is canonical, has a nonce and certificate request, and binds the SHA-256 imprint of the entire signed **commitment**, not just the raw dataset. The witness retains the original 0600 request as private audit evidence. **No network or TSA submission is performed.** This phase returns only `PASS_OFFLINE_UNSIGNED_TIMESTAMP_QUERY_ONLY` and false authorization flags.
7. **A separate authorized human witness**, not this program, submits the TSQ to an **independently approved TSA**, retains the genuine returned DER `.tsr`, independently authenticates the TSA trust root/certificate policy and revocation status, and obtains independent evidence of issuance **before any first model inference**. Do not disclose raw case records, gold, keys, host logs, or private alias maps to the TSA; the TSQ contains only cryptographic imprint, nonce and issuance options.
8. Only **after** the TSA returns: compute `"sha256:" + SHA256(original_response_der)` as the witness anchor reference. The author/reviewer signatures are kept unchanged. The independent witness signs the existing #156 `role_payload("witness", commitment, {author,reviewer}, anchor_reference)` and packages all **three** signatures in the existing `lfm3.custody.three-role-handoff.v1` bundle. Run the *unchanged* #159 `scripts.lfm3_rfc3161_anchor_verify` with original query/response and an independently authenticated TSA CA chain. Cryptographic PASS is still not proof of external key registration, trusted TSA time, or first-inference chronology.

## Private-file CLI (offline only)

Run on a **Linux custodian host** with safely provisioned, authorized fictional research files inside owner-owned 0700 private directories. Inputs must be `0600` or `0400`, owner-only, single-linked ordinary JSON files. Output must be a **new** absolute file beneath a verified private 0700 directory; no overwrites or symlinks are permitted. Neither file contents nor source dataset digest are printed.

```bash
PYTHONPATH=. python3 -m scripts.lfm3_prepare_prewitness_tsq \
  --dataset /approved/private/hidden-cases-and-gold.json \
  --prewitness /approved/private/author-reviewer-signatures.json \
  --key-roster /approved/private/externally-authenticated-three-role-keys.json \
  --tsq-output /approved/private/witness-nonce-request.tsq
```

The CLI uses the already verified #165/#166 safe input and output directory handles, strict bounded duplicate-key JSON decoding and exclusive `0600` write with inode-checked rollback on a normal local error. It does not send the query, generate keys, print gold/source, read production credentials, change an endpoint, or permit model inference. A valid TSQ can be inspected locally with:

```bash
openssl ts -query -in /approved/private/witness-nonce-request.tsq -text
```

Never place confidential case notes, gold, private keys or hidden model responses in GitHub. The original TSQ SHA-256 digest should be separately witnessed **before any future inference**, as should the external `.tsr` once received. The public frozen source-contract FreeTSA timestamp from #160 is *not* a case-corpus seal, and cannot be reused for this purpose.

## Actual synthetic contract acceptance and remaining gates

Tests create ephemeral **non-independent** Ed25519 keys in memory, use actual local OpenSSL to generate and decode the RFC3161 query, create **synthetic locally issued** X.509 TSA certificates and a signed test response, then sign that response with the synthetic witness and pass it to the existing unchanged final three-role and timestamp verifier. The end-to-end contract has a real cryptographic query, signature, nonce and response **but no independent identity or external time evidence**. Further tests reject changed cases/gold, wrong model/prereg, reused signing keys, missing/forged roles, premature witness fields, unsafe private input/output permissions, symlinks, missing files and output collisions.

Only the order-of-operations implementation is completed. No real author, reviewer, witness key enrollment, externally timestamped **new** hidden dataset, independently verified chronology, genuine LFM3 model execution, quality result or production authority has been obtained. Every result has `external_timestamp_token_received=false`, `pre_inference_seal_verified=false`, `external_case_custody_verified=false`, `permitted_to_infer=false`, `model_value_verified=false` and `production_authorized=false`. Keep issue #157 external custody and production **HOLD**, PR draft/unmerged, and do not circumvent any blocked inference invocation.
