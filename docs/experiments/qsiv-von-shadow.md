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


## Explicitly gated Von Choice local-wire adapter (draft experiment)

This repository also contains:
- experiments/von_choice_shadow.py: stdlib-only adapter for an **already-running, operator-controlled** Von server bound to 127.0.0.1.
- tests/test_von_choice_shadow.py: simulated HTTP server plus strict validation tests; simulated responses are **not actual inference**.
- experiments/fixtures/von_choice_curated_synthetic.jsonl: entirely fictional four-row case fixture, option permutation and missing-evidence case.

Run non-network validation FIRST:

    python3 experiments/von_choice_shadow.py experiments/fixtures/von_choice_curated_synthetic.jsonl

Do NOT execute against a live Von server until an operator separately pins weights/calibration, ensures loopback-only service binding, disables silent truncation with VON_ON_OVERFLOW=refuse, and approves CPU/RAM/disk budgets. The command below is illustrative; CI does not run it:

    python3 experiments/von_choice_shadow.py experiments/fixtures/von_choice_curated_synthetic.jsonl \
      --execute --ack-sanitized --ack-preloaded \
      --model-revision von-1.3.0 --url http://127.0.0.1:18051 \
      --out /tmp/von-choice-observations-unique.jsonl

**Calling a preexisting Von server may trigger its lazy multi-GB weight download/load.** The adapter never starts a server or installs packages/weights itself. Acknowledgment flags do NOT attest that the model was already loaded; they require the operator to accept this risk. This experiment sends only operator-curated fictional cases, no real fleet secrets or user data.

The HTTP contract derives from official upstream wfzyx/von files src/von/server.py, src/von/engine.py, and src/von/types.py reviewed 2026-10-11. The adapter checks version von-1.3.0, a single Choice answer and a complete finite probability distribution. It rejects both body-level and header-level truncation. Its stored confidence is the probability of the chosen option, not necessarily the response's separately named confidence metric.

Only Choice is in scope. Noul/Score need separate semantics and calibration review. Pattern filtering cannot guarantee text is sanitized: inputs require independent manual review. The adapter outputs allowlisted records to a **new /tmp file** (mode 0600), never appends or overwrites, and cannot route workloads.

The comparison scorer can consume these new observations when joined to separately generated observations from matching, independently labeled cases. A local HTTP response is not physical model execution attestation, and source_kind/model_revision fields remain declared assertions.

**As of this checkpoint, no actual Von model inference was performed on x1-370**; all protocol tests use an intentionally fake HTTP server.

**Port collision verified on x1-370 (2026-10-11):** 127.0.0.1:8000 is an AssistX API listener, NOT Von. The adapter therefore has no default --url and requires an operator-selected port (example 18051 is illustrative, NOT a confirmed available or serving port). Before any POST, it performs a GET /health and requires service=von-decision-server and engine=von-1.3; mismatches produce no model request and no output evidence file. This is *process-type identity only*, not hash-pinned weight attestation or proof that the server was preloaded. Never bind Von to an occupied production port.
