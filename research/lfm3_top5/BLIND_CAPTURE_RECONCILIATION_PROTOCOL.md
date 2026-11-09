# LFM3 custodian-only alias restoration for four-session research scoring

**Research-only, no model calls, no production changes, no source custody approval.** This completes the local structural path from the gold-free opaque-ID packet in stacked draft PR #162 back to the unchanged four-stage repeatability and frozen prospective scoring contracts (#152, #153 and #158). No new independently authored sealed dataset or model result exists.

## Why the reverse mapper is needed

The model-facing packet deliberately contains **opaque HMAC aliases and fictional source notes only**. It does NOT contain original case IDs, `expected` gold labels, challenge tags, canonical signed dataset hash, secret blinding key, or alias map. The private custodian separately holds the original signed dataset plus the restricted 0600 `opaque_id_to_original_id` receipt.

After a future independently approved four-session research capture, **only the custodian** may restore aliases to original IDs for comparison with the full sealed oracles. The model operator must never be given that private mapping, the gold dataset or full source digest. Both preparation and reconciliation scripts are strictly offline and do not themselves authorize an inference call.

The new pure module `blind_capture_reconciliation.py`:

1. Rechecks the original full corpus, gold labels, frozen preregistration and **all three Ed25519 signer roles** using the unchanged `independent_custody_handoff.verify_handoff`. This provides crypto **structure only**, not independently trusted identities or a pre-inference timestamp.
2. Verifies exact model-facing packet schema, 36+ balanced source cases, expected checkpoint SHA, false custody/release flags, and a byte-linked private custodian receipt with the same canonical signed dataset digest. It requires unique opaque 25-character IDs, a **one-to-one** alias map covering all original case IDs, no missing/extra source rows, and exact source-note equality. It rechecks obvious category/gold-label leaks in text. The text filter is conservative and cannot replace an independent human leakage review.
3. Accepts only a four-stage **opaque** capture with `schema: lfm3.prospective.blind-four-stage-capture.v1`, `blind_prompt_view_sha256` matching the packet, the pinned checkpoint SHA, and both release flags false. Its nested `stages` retain the exact frozen #153 record requirements, except that their `requests[].case_id` values are **opaque aliases**. No gold labels are in this capture.
4. In memory only, map each request alias to its original custodian-controlled ID and restore the frozen #153 `lfm3.prospective.stability.v1` capture with original signed dataset hash. This retains original request IDs, response digest, raw response, timestamp and stage resource/cleanup claims without changing model output or selecting a favorable reply.
5. Reuse the unchanged #158 `reconcile_primary` API, which independently runs the full four-stage repeatability checks and unchanged #152 primary score with deterministic case-rank selection. A missing stage, replayed ID, bad cleanup, mismatched note/digest, swapped alias, forged authority, invalid signature or failed scorer returns HOLD.
6. The successful result is **`PASS_OFFLINE_BLIND_CAPTURE_RECONCILIATION_ONLY`** and includes only counts plus a blind-packet digest. It does NOT reveal source notes, original case IDs, gold labels, private alias map, full source digest or raw predictions. It always reports `independent_author_verified=false`, `external_case_custody_verified=false`, `pre_inference_seal_verified=false`, `actual_model_execution_verified=false`, `model_value_verified=false`, `permitted_to_infer=false`, `production_authorized=false`.

## Offline CLI

On the authorized *private custodian machine* only, with independently approved fictional research source material:

```bash
PYTHONPATH=. python3 -m research.lfm3_top5.blind_capture_reconciliation \
  --manifest /approved/custodian/frozen-prereg.json \
  --dataset /approved/custodian/original-cases-and-gold.json \
  --bundle /approved/custodian/three-role-signatures.json \
  --key-roster /approved/custodian/preauthenticated-public-key-roster.json \
  --packet /approved/custodian/blind-model-facing-packet.json \
  --receipt /approved/custodian/private-alias-map-receipt.json \
  --capture /approved/custodian/blind-four-stage-capture.json
```

Exit 0 means **offline structural score reconciliation only**. Exit 2 plus `HOLD` means incomplete/mismatched input. The CLI prints only a redacted summary and does not create files, run inference, connect to GitHub/TSA, modify an endpoint, or release cases.

The unit fixtures use **fictional, self-generated memory-only signers and fabricated model replies** and assert a 36-case, four-stage, 144-request structural pass plus 72 primary-scored outputs. Those observations **are NOT 36 new actual model generations** and DO NOT measure generalization or independent accuracy.

## Blocking external conditions

Issue #157 remains HOLD for separately authenticated author/reviewer/witness identities, independent case/oracle custody and sanitization, approved trusted TSA issuer and a *new* sealed-corpus timestamp issued before the first real model inference. The earlier FreeTSA receipt in PR #160 timestamps only the **public frozen source contract**, not an independent case corpus. Neither model-output agreement (#161) nor this structural unblinding code permits a real inference run or fleet/production decision. Keep drafts unmerged and all signing, provider, public ingress and production boundaries unchanged.
