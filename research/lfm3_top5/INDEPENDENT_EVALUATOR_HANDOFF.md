# Independent narrative evaluation — role-separated handoff (research only)

**Purpose:** make the *next* evaluation independently reproducible rather than continuing to tune on the 36 known developer-authored narratives. No independent case package, source witness, timestamped anchor, or real production authorization exists yet. This protocol does not create any of them by itself.

## Frozen technical contract

- Base preregistration is `prospective_prereg_v1.json`, SHA-256 `38aeb6eba605cfff44f6d46e15b0afe23b9fbe0b405561ae7848e1124d645696` from research-only draft #152.
- Frozen deterministic rule SHA-256: `29ecfbfdcd3c5606690718e0416f093e737609187f5e527ab4c0625089d3b18e`; frozen scorer SHA-256: `d80c06ccf891aa680e65c160abfe4b4141a5dc0f6f6c5c957d061ac193e7c101`. Model checkpoint SHA-256: `43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452`.
- At least **36 independently authored, distinct, sanitized/fictional notes**, with >=5 cases per all six category labels and >=5 per challenge: negation, chronology, ambiguity, distractor, missing evidence, overlap. Each complete JSON row has `id`, `note`, `expected: {category,evidence}`, and `challenges`, matching the frozen `lfm3.prospective.cases.v1` schema.
- Thresholds were fixed **before** the independent cases: >=85% exact and >=15 percentage points absolute gain over frozen rule, zero wrong positive attributions, and perfect correctness on insufficient-evidence cases **per arm**. This is a research screen, not permission to release.

## Separation-of-duties procedure

1. **Author** (not the model developer or rule author) creates new fictional or adequately sanitized incident notes. Do not inspect the 36 known development cases, old model failures, experimental prompts or model outputs. Keep cases private from inference operators before the sealed study.
2. **Gold reviewer** (distinct human/operator from author and model developer) adjudicates category and the *specific supporting source marker*, including appropriate abstentions, without reading candidate model outputs. Resolve disagreements before freezing; then do not alter the oracle after seeing results.
3. **Key custodian** separately authenticates the identity and Ed25519 public key for author, reviewer, and witness; ensure three distinct real owners and separate private keys. Publish a trustworthy roster fingerprint via an independently controlled approved channel. Never paste private keys, credentials, raw private incidents or signatures generated from private keys into public GitHub issues or hosted model prompts.
4. **Witness** (separate from both and independent from the model operator) obtains a trusted, externally timestamped *prior* commitment for the canonical dataset digest and frozen prereg hash. Independently validate third-party timestamp / transparency record, issuer, revocation, chronology and publicly reviewable immutable reference. A text reference in JSON is **not** proof of external anchoring.
5. Author and reviewer sign **role-separated commitment statements** bound to dataset hash, prereg hash and model checksum, using external key material not created by this module. Witness signs those two signatures and claimed anchor reference. Use `independent_custody_handoff.py` to verify the signatures locally with an **independently authenticated** three-key roster; this yields `PASS_OFFLINE_SIGNATURE_STRUCTURE_ONLY`, not external-custody PASS.
6. A different evaluator must verify real signer identities, registered roster keys, original signed statements, timestamp authority and proof that the anchor **predates any model inference**. Keep these author/reviewer/witness custody receipts under a controlled evidence store. Until independently witnessed, **HOLD** real source custody.
7. Only after that custody gate is independently reviewed should authorized operators execute identical frozen case IDs under `prospective_holdout_gate.py` and four complete repeated stages from `session_stability_gate.py`. Use bounded admission/cleanup checks, separate CPU/Vulkan starts and request IDs, preserve actual model and runtime hashes, and keep raw host receipts locally. Scoring and independent value review remain separate.

## Offline verifier inputs

Run on an *authorized* evaluation computer with read-only local files:

```bash
PYTHONPATH=. python3 -m research.lfm3_top5.independent_custody_handoff \
  --dataset /approved/private/cases.json \
  --bundle /approved/private/three-role-handoff.json \
  --key-roster /approved/read-only/preauthenticated-roster.json
```

The verifier neither generates keys nor contacts a network or writes an output file. Its JSON output never includes case text or secret keys. The test suite generates **ephemeral memory-only synthetic keys**; the fact that a self-generated test bundle passes proves signature-verification code works, **not** signer independence. Do not use test keys as trusted evidence.

**Required bundle fields:** `schema: lfm3.custody.three-role-handoff.v1`, `commitment: {schema: lfm3.custody.dataset-commitment.v1, dataset_sha256, prereg_sha256, checkpoint_sha256}`, `signatures: {author:{key_id,signature_hex},reviewer:{key_id,signature_hex},witness:{key_id,signature_hex}}`, `claimed_external_anchor_reference`. The separate roster is `schema: lfm3.custody.trusted-keys.v1` plus `roles` with per-role `key_id` and `ed25519_public_key_hex`. Exact signatures are Ed25519 over the documented domain-separated canonical UTF-8 bytes produced by the module's `role_payload` function. The witness signs the commitment plus author/reviewer signature hex and the claimed external anchor reference. The verifier rejects mismatched sources, keys, signatures or extra authority fields.

**Never conflate these gates:** Cryptographic source commitment is not independent identity; witness signature is not a trusted external timestamp; local DRM counters are not signed tensor execution; test-pass is not held-out accuracy; model output is not deployment permission. `model_value_verified`, `external_custody_verified`, `pre_inference_seal_verified`, and `production_authorized` remain **false** on every code path.
