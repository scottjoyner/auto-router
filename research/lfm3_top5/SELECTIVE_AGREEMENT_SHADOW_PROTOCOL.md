# Selective LFM3 + frozen-rule agreement shadow — fictional development only

**Research-only offline shadow**, not a model endpoint, a router, a source-of-truth adapter, a custody witness, or deployment authorization. This change does **not** modify the frozen #152 scorer, #153 repeated session contract, #156 signature verification, #158 primary selector, #159 RFC 3161 verifier, #160 public timestamp, production runtime or inference weights.

## Engineering hypothesis

The local 36-case **developer-authored** fictional development comparison from November 9 (original prompt, QAD Q4_0 checkpoint, CPU/Vulkan8) found **19/36** fully exact LFM3 predictions, **18/36** exact frozen rule predictions. The methods do not show independent quality advantage. On these *same developer-authored examples*, they agreed on exact category and evidence marker in **12/36**; those twelve happened to be correct, but **five** are the mutual `insufficient_evidence` abstention class. Only **seven positive classifications** matched, and all seven matched the developer-authored gold. This is retrospective and selection-on-agreement, not an independent precision estimate. Seven cases are too few for a production safety claim.

The proposed *fictional research-only* selective-response policy is narrower than the whole 12-case agreement set:

1. Accept only an exact-shaped, caller-declared `fictional_synthetic_development` observation containing a bounded note and supplied **previously captured** raw LFM3 reply. Reject any production/private/unknown class, forged release field, independent custody claim or mismatched asserted checkpoint. A model identity assertion is not independently attested model execution.
2. Before every evaluation, verify that `prospective_rule_v1.py` exactly matches the frozen SHA-256 `29ecfbfdcd3c5606690718e0416f093e737609187f5e527ab4c0625089d3b18e`. **Never tune or rewrite the frozen rule** based on model output.
3. Require unique real source markers `E1`–`E9` in the fictional note, parse LFM3 response with duplicate-key/non-finite/truncation rejection, enforce exactly two fields (`category`, `evidence`) and a *single* `E#` marker or permitted null for `insufficient_evidence`. A value such as `"E2 (verified): ..."` is a **schema violation**, even if valid JSON.
4. Compare the model's exact category **and evidence marker** with the pinned deterministic rule's exact output. Disagreement or malformed input => `HOLD`, no rule-only or model-only fallback and **no retries**. If both select `insufficient_evidence`, also `HOLD` (correct abstention is not a positive incident diagnosis).
5. Only an exact positive agreement returns `PASS_UNTRUSTED_RESEARCH_AGREEMENT_ONLY`, with `candidate_for_manual_review=true`, not permission to act. It cannot authenticate the caller's source, live inference identity, model quality or real fleet data, and it never performs inference or I/O beyond reading the frozen rule's source hash. Every result still has `safe_to_act=false`, `independent_source_custody_verified=false`, `actual_model_execution_verified=false`, `model_value_verified=false`, `production_authorized=false`.

## Verified retrospective development counts, not held-out evidence

One actual previously captured original-prompt response per fictional case/mode (36 unique cases, two modes; 72 primary model outputs, **zero new inference calls**) was reprocessed in the local-only raw receipt archive. The strict posthoc output breakdown was **identical per CPU and Vulkan8**:

| Shadow outcome | Cases per mode | Correct vs dev-authored gold |
|---|---:|---:|
| Exact positive model–rule agreement => manual review only | **7/36** | 7/7 observed |
| Exact mutual `insufficient_evidence` => HOLD | **5/36** | 5/5 observed |
| Category/evidence disagreement => HOLD | **16/36** | Not a quality metric |
| Syntactically valid JSON but illegal `evidence` value => HOLD | **8/36** | 0 fully exact |

**Correction to prior reporting:** All **36/36** original replies per mode were parseable JSON objects with two keys, but **8/36** per mode placed explanatory text in the `evidence` value instead of the exact `E#` marker required by the task. Thus **28/36** satisfy the strict semantic output schema; the historical `invalid_format=0` refers only to loose JSON shape, not strict marker conformance. This does **not** alter the historical **19/36** exact-answer score, which already counted those eight as incorrect. Do not silently reinterpret the frozen scorer's `invalid_format` field: this shadow pass is an independent supplementary *schema-veto* check, not a change to the frozen scoring definitions.

Local-only audit V2: `/home/scott/git/.lfm3-dev-cohort-20261108/dev_shadow_agreement_summary_v2.json`, SHA-256 `8b887c560e96384d786440d1ab5ecd9c2f62a0a21a3a732513a0b023782012ad`, private local Git commit `c19422d8ccee93eeec63ca924df23de63fa123fa`, **zero remotes**. Raw model responses, private host receipts and complete fictional notes are deliberately **not published**. The report includes only derived counts and local receipt hashes.

## Safe next steps

- Keep #157 independent author/gold-reviewer/externally authenticated timestamp-before-inference custody HOLD. This shadow experiment **cannot** satisfy it or repurpose the public-contract timestamp as a sealed case-corpus token.
- Independently author and adjudicate *new* sealed cases before any model request. Measure agreed-positive coverage, **wrong positive agreement** rate, abstention behavior, disagreement failures, and strict schema violations independently. An agreement can be confidently wrong; treat zero observed errors in seven handpicked cases as weak evidence.
- Only after independently held-out quality and source trust are established could a separate human-approved integration discussion begin. This code has no production interfaces. Do not merge, deploy, change providers, route real user data, grant credentials, or enable ingress based on this research PASS.
