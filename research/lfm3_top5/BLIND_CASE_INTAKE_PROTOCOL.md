# LFM3 role-separated blind-case intake (offline, research only)

This project now has an explicit **gold-label separation step** for the *future* independent evaluator. It does **not** create independent cases, unlock inference, authenticate an external witness, verify source confidentiality, or change any production code. Draft research-only, no inference or network.

## Purpose and boundaries

The pre-existing independently signed case-set schema `lfm3.prospective.cases.v1` holds a private `note` **alongside** private `expected` (category and evidence-marker gold answers) and `challenges` tags. Giving that file to an inference operator risks leaking the gold and contaminating a blinded evaluation. A prior experiment's self-authored 36 cases are not a valid independent dataset. This tool handles a future **fictional research-only** case set. It does not accept private/real incidents, production data classes, or a claim that a human already approved inference.

`research/lfm3_top5/blind_case_intake.py` performs only pure local operations:

1. Require explicit self-declared `fictional_synthetic_research`. This declaration **does not establish actual confidentiality**. For independent source custody see issue #157.
2. Reuse the untouched three-role Ed25519 signature verifier from draft #156. Missing/corrupt case note, gold label, dataset shape, signer roster, reviewer signature, model checksum or frozen prereg identity => **HOLD**. A valid self-signed test bundle demonstrates only structural cryptographic integrity, **not** independently enrolled signers.
3. Conservatively screen original notes for explicit category code names or `expected:`, `gold answer:`, `gold label:`, `ground truth:` or `oracle:` hints. Those are likely answer leaks. This heuristic is **not** complete semantic leakage detection; an independent blinded reviewer must inspect the note content before release. It intentionally blocks the previously used synthetic test fixtures that literally contained `category=<true label>`.
4. Generate **opaque `B` + 24 uppercase hex character aliases** using `HMAC-SHA256` keyed by a custodian-only 32-byte random secret, for each original case ID. Sort by alias, not original ID. Both category-grouped or sequential source identifiers and original-case order are hidden from the model-facing packet. A collision => HOLD. The CLI generates a fresh secret from the OS CSPRNG; source code has no hardcoded production keys. The secret itself is neither exported nor logged.
5. Write a model-facing packet with only a case's **opaque alias** and original note text. No `expected`, `challenges`, source dataset hash, original IDs, private signer keys, custody receipt or answer labels are present in the packet's structured fields. It includes a pinned expected model checkpoint and `external_custody_verified=false`, `permitted_to_infer=false`, `production_authorized=false`. Notes can still contain clues not caught by the explicit gold-hint filter.
6. Separately retain a **custodian-only receipt** with the full canonical dataset commitment hash, blinded packet hash, case count and secret alias-to-original-ID mapping for later blinded scoring. Do not send this receipt, full dataset, gold labels, source signer roster or blinding key to an inference operator.
7. Both the pure function and CLI return only **`PASS_OFFLINE_GOLD_FREE_PROJECTION_ONLY`**, never a readiness-to-infer or deployment approval. Every result says `independent_author_verified=false`, `external_case_custody_verified=false`, `pre_inference_seal_verified=false`, `permitted_to_infer=false`, and `production_authorized=false`. A separate trusted human custodian must evaluate external timestamp-before-inference, independent signer identity and authorization before *any* future case inference.

## Restricted CLI

Run only on an authorized **local** custodian machine with fictional synthetic material and Python 3.12 + cryptography:

```bash
PYTHONPATH=. python3 -m scripts.lfm3_prepare_blind_packet \
  --dataset /approved/private/sealed-cases.json \
  --bundle /approved/private/three-role-signatures.json \
  --key-roster /approved/private/separately-authenticated-roster.json \
  --source-class fictional_synthetic_research \
  --packet-output /approved/private/model-facing-no-gold.json \
  --receipt-output /approved/custodian-only/alias-map-receipt.json
```

The CLI is opt-in, makes **zero network/inference calls**, never imports production runtime, never reads credentials, and prints only the decision, checksum and count. It refuses absent or invalid source, private/unknown data class, duplicate paths, existing output files or symlinks, oversized inputs and missing signing material. It uses exclusive, no-follow `0600` file creation and removes newly created files if the second write fails. There are **no automatic provider calls or retries**. The custodian is responsible for separately secured parent directories, restricted access, source validation, private alias receipt custody and actual external authentication. Both outputs remain private; do not upload them to GitHub.

## Independent-evaluation discipline

A blind projection is not itself a trusted pre-inference seal. It **must not** be used to run a case set until an independent author and gold reviewer are enrolled, the source and gold adjudicated, an independently approved signer and RFC3161 timestamp authority witnesses the exact corpus commitment *before* any case inference, and an authorized custodian checks the timestamp and custody chronology. This is tracked by issue #157.

For a subsequent evaluation, the model-facing process may see **only** the note packet with opaque aliases. Four-stage captured responses must be reconciled back to original case IDs by the **private custodian** using the restricted map before the unchanged scorer and repeatability contracts from #152/#153/#158 apply. A synthetic local green test must never be substituted for a real independent trial or for SGNC/GPU/production attestation.
