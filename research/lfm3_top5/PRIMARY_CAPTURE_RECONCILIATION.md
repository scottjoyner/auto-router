# Frozen primary-score reconciliation from four repeated sessions

**Research only; no independent custody, source authority, inference, or production action.**

## Why this exists

The original [draft #152 prospective scorer](https://github.com/scottjoyner/auto-router/pull/152) accepts exactly one scored output **per case and mode** but requires at least two distinct session IDs in each mode. The [draft #153 four-session capture](https://github.com/scottjoyner/auto-router/pull/153) records **each case twice per mode** in ordered stages `cpu`, `vulkan8`, `vulkan8`, `cpu`. Without a frozen bridging rule, an evaluator could cherry-pick the better answer from either run after observing correctness.

## Exact deterministic selector (v1)

This selector is frozen in `primary_capture_reconciliation.py` before independent cases exist:

1. Sort all case IDs lexicographically. IDs are unique, constrained by the existing prospective case schema, and must have >=36 cases.
2. For rank 0,2,4,... select that case's **first** stage within each mode. For rank 1,3,5,... select the **second** stage within that same mode. No response, expected label, model accuracy, timestamp, stage latency or case challenge affects the assignment.
3. Thus the selected primary set contains **one response per case per mode**, with half of each mode's cases drawn from each of its two stage identities. The dataset contributes N independent **case identities**, not 2N or 4N. A different case ID ordering is a new preregistered experiment; do not change the selector after inspecting outcomes.
4. Run the *unchanged* #156 three-role signed-bundle structural verifier, then the *unchanged* #153 complete four-stage repeatability audit, then the *unchanged* #152 frozen scorer over the selected primary outputs. Any incomplete step returns **HOLD** with no inferred permissions. Raw model outputs are never returned by the integration summary.
5. Record primary metrics and repeatability metrics separately, including wrong answers, correctness flips, and any attempted release-authority spoofing **in either selected or nonselected repeats**. A structurally passing result never conceals wrong model answers. Separate quality indicators are *advisory*, not a validated model-value claim.

## Offline CLI

Once an independently managed approved evaluator supplies five compatible private files (frozen prereg, dataset, signed bundle, separately authenticated public-key roster, full four-stage capture), run locally:

```bash
PYTHONPATH=. python3 -m research.lfm3_top5.primary_capture_reconciliation \
  --manifest /approved/private/prereg.json \
  --dataset /approved/private/cases.json \
  --bundle /approved/private/signatures.json \
  --key-roster /approved/private/trusted-key-roster.json \
  --capture /approved/private/four-stage-capture.json
```

Exit 0 means **offline structural reconciliation only**. Exit 2 and `HOLD` mean at least one contract failed. It prints a canonical JSON summary with hashes and counts, never raw notes or raw model answers. All files are read-only; no signing, inference, network requests, live key access, or report files are performed.

## Boundaries and next independent actions

- `PASS_OFFLINE_RECONCILIATION_CONTRACT_ONLY` certifies only that *caller-supplied*, offline files obey the combined software contracts. It does not certify real inference execution, signed GPU tensor activity, original key ownership, source authorization, third-party timestamp-before-inference, independently authored cases, presealed corpus, representative accuracy or legitimate release approval.
- On every code path `independent_author_verified=false`, `trusted_key_ownership_verified=false`, `external_anchor_verified=false`, `pre_inference_seal_verified=false`, `external_custody_verified=false`, `model_value_verified=false`, `accelerator_attested=false`, and `production_authorized=false`.
- Synthetic tests generate ephemeral signing keys in memory. They never create an independently trusted roster or real external anchor; tampered evidence and fake production claims are held or separately flagged.
- Actual outside participants, key custodians and a trusted independently timestamped case seal are required per [external dependency #157](https://github.com/scottjoyner/auto-router/issues/157) **before any new blinded inference**. No new case cohort or hosted third-party model should be used as a substitute for those controls.
- Only the specific frozen rule, scoring, signer and stage contracts are run. No production `src/`, credentials, network requests, router selection, API calls, model-weight changes or deployments. Draft and unmerged until independently witnessed gates are satisfied.
