# QSIV / Von typed-decision shadow experiment

**Status:** experimental, source-only, non-authoritative. Related: [issue #43](https://github.com/scottjoyner/auto-router/issues/43).

## Verified constraints

- [QSIV](https://huggingface.co/djinn-san/QSIV) is a 752M Qwen3.5-0.8B-derived Choice/Noul/Score model. Its shipped FP8 production runtime requires Blackwell sm_120/CUDA; the BF16 reference path is a research starting point, not verified ROCm/MPS/RTX3090 support.
- [Von](https://github.com/wfzyx/von) is a 395M ModernBERT decision model, advertised with OpenVINO CPU, CUDA, ROCm and MPS backends. Its default state limit is 8192 tokens, with truncation; set VON_ON_OVERFLOW=refuse for an exact comparison.
- Read-only x1-370 check (2026-10-11): Ryzen AI 9 HX 370, 24 logical CPUs, ~55 GiB RAM available, no installed von-sdk, cached Von weights or Von container identified in shallow inspected paths. Other nodes were not inspected.
- Auto-router main assigns canonical scheduling and physical model identity to AssistX, not this gateway; deterministic eligibility is unchanged.

## Files

- experiments/typed_decision_shadow.py: pure Python stdlib JSONL aggregate scorer; imports no router services, GPU libraries or network clients.
- tests/test_typed_decision_shadow.py: validation, safety, provenance-cohort, permutation and paired-statistic regression tests.
- experiments/fixtures/typed_decision_synthetic.jsonl: **fabricated** rows; model names QSIV/Von are illustrative labels, not inference evidence.

## Run offline

    python3 -m unittest discover -s tests -p 'test_typed_decision_shadow.py' -v
    python3 experiments/typed_decision_shadow.py experiments/fixtures/typed_decision_synthetic.jsonl --baseline baseline > /tmp/typed-decision-shadow-synthetic.json

No output goes to NAS by default. Never feed raw prompts, context documents, private IPs, credentials, storage UUIDs, location traces or unredacted router logs.

## Observation schema

Exactly one row per (case_id, model_id, variant), all fields required:
- Opaque case_id, model_id, model_revision, and state_sha256 of canonical **sanitized** state.
- task_type: choice/noul/score. variant: original/permuted/missing_evidence/contradiction.
- Independently reviewed gold, eligible_options, evidence_complete.
- Candidate prediction, confidence (probability of chosen answer, not fabricated), latency_ms, and status (ok/unavailable/error).
- source_kind: synthetic/replayed_trace/model_observation. **Declared metadata is not independent provider attestation.**

Rejects extra fields, contradictory labels across models on the same case, mixed model revisions or source cohorts, duplicate rows, invalid confidences, fabricated predictions on failed requests, and permutations without an identical original contract.

Report: answered coverage; accuracy-on-answered; unavailable/error cases; output outside admissible choices; outputs without independent evidence; option-permutation instability; 10-bin ECE; p50/p95/p99 on reported timings; and exact two-sided paired McNemar counts. The report always declares execution_attested=false and promotion_approved=false.

**Limits:** A changed permutation label in JSONL does not prove the external adapter actually reordered options. The scorer does not verify that source_kind=model_observation was produced by QSIV/Von. McNemar alone is not a promotion gate. Score-type ordinal distance is not computed. An ungrounded output counter includes even a negative Noul prediction lacking authority facts; it is a diagnostic, not proof of an executed unsafe action.

## Next evidence gates (not met)

1. Pin exact weights, calibration, runtime, tokenizer, checksums and hardware for every evaluated model.
2. Independently label at least 100 sanitized, matched cases; set aside a holdout and invalid/stale/missing authority cases.
3. Optional isolated Von OpenVINO CPU pilot first; prevent silent >8K truncation. Disable multi-step chains if measuring single-forward-pass performance.
4. QSIV BF16 AMD feasibility spike is separate; never assume Blackwell FP8 benchmark numbers apply.
5. Only opt-in adapters consume confirmed model responses; retain independently authenticated request ownership and physical execution witnesses outside the model's reply.
6. Zero eligibility breaches plus measured quality, tail latency, memory, energy and operator approval are prerequisites to any canary.

No new inference processes, live routing policy, agent dispatch, permissions, admission/claims, database schema, background scanner, NAS writes, or hosted providers are introduced.

Keep [PR #35](https://github.com/scottjoyner/auto-router/pull/35), [PR #36](https://github.com/scottjoyner/auto-router/pull/36), [PR #42](https://github.com/scottjoyner/auto-router/pull/42), and [issue #37](https://github.com/scottjoyner/auto-router/issues/37) independently gated. A configured route, model prediction and physical model execution are distinct facts.
