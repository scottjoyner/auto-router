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


### Comparable Von CPU configuration (reference only; not started)

The upstream Von 1.3 backend enables chain-of-options by default. This can execute several model passes and would make a one-pass QSIV/Choice latency comparison misleading. Official Von backend and CLI support disabling it with VON_CHAINS_DIR=off / --no-chains. Before any permitted physical trial, use a **dedicated service on an independently verified free loopback port**, not AssistX's active port 8000:

    VON_CHAINS_DIR=off VON_ON_OVERFLOW=refuse \
      von serve --host 127.0.0.1 --port 18051 \
      --device openvino:cpu --no-chains --on-overflow refuse

This command is **documentation only**: its dependencies/weights are not installed or preloaded, and its example port 18051 is not confirmed available. Running it can download multi-GB weights and consume significant CPU/RAM/SSD; obtain operator resource admission before startup. The adapter's /health check identifies the server type, but does not prove it was started with --no-chains or --on-overflow refuse, nor does it attest weight/calibration hashes. Capture those facts separately with an immutable startup manifest. Keep all reports experimental regardless of observed confidence or latency.


## Eligibility is NOT the option list — security correction

The model-facing Choice criteria dictionary may include INELIGIBLE, offline, unauthorized, or stale candidates. Merely listing a candidate as a Choice answer does NOT authorize routing there. Input fixtures now require a separate eligible_options list furnished from independently authoritative deterministic policy. It MUST be a subset of the displayed criteria keys.

- In fleet_sample_01, the displayed criteria includes node_a and node_b, but only node_a is eligible. A Von prediction of offline node_b, even at 99% confidence, is counted in ineligible_outputs and never admitted by this tool.
- Incomplete authority evidence always requires eligible_options=[] and produces no automatically admissible model choice.
- Permutations MUST preserve gold, input state and independently authorized eligible_options.
- Missing eligible_options fails closed; the adapter never infers approved nodes from what the model may compare.
- The scorer is advisory. For real data, approved options must come from separately authenticated, fresh authoritative policy; do not take them from LLM outputs, client-supplied metadata, catalog visibility or stale reports. This experiment DOES NOT implement the trusted producer.

### Reproducible mechanical challenge — synthetic only

Source-only experiments/build_synthetic_decision_challenge.py generates 144 original cases across nine groups (offline, expired lease, LOADED vs AVAILABLE, capacity, authenticated owner, stale health, effective provider authorization, contradictory later update, missing evidence), plus 72 option-order permutations: 216 rows total. Of 144 originals, 128 have mechanically supported positive route labels and 16 have intentionally incomplete authority. Labels are generated by a static policy table and are NOT independently reviewed operational ground truth. Nodes and state descriptions are invented.

Run locally with disposable /tmp files:

    challenge_dir=$(mktemp -d /tmp/decision-challenge-XXXX)
    python3 experiments/build_synthetic_decision_challenge.py \
      --cases-out "$challenge_dir/cases.jsonl" --oracle-out "$challenge_dir/oracle.jsonl"
    python3 experiments/von_choice_shadow.py "$challenge_dir/cases.jsonl"
    python3 experiments/typed_decision_shadow.py "$challenge_dir/oracle.jsonl" \
      --baseline policy_oracle

The policy oracle stores no fabricated latency or confidence, and its observation source kind is synthetic. Its 128/128 correct labels are **an internal consistency test**, NOT evidence of actual QSIV/Von accuracy, not verified routing performance, and not physical execution.

The opt-in Von adapter defaults to a maximum FOUR requests on --execute. Passing the 216-row corpus without an explicit larger --max-requests refuses execution BEFORE /health or any inference POST. This bounds accidental CPU load; it does not attest weights, memory budgets, preloading or authorization.

**NO-GO remains:** independently review 100+ separate real/held-out matched cases; pin exact weights, calibration, model/backend and code hashes; observe the same sanitized cases; record actual CPU/GPU, RAM, energy and latency; independently attest user ownership and physical model execution. The generated corpus alone can NEVER satisfy these gates.


## Exact question-contract binding (paired-evaluation integrity)

Every observation now requires BOTH a sanitized state_sha256 and a question_sha256. The Von adapter hashes the normalized instructions and an ORDERED list of (option key, option description) pairs, so changing wording, available answer descriptions or display order changes the question contract.

- For comparisons on the same case ID and variant, the scorer refuses a pair if model A and model B have different state digests, independent gold, eligible_options, or question digests.
- For an original/permuted pair, the scorer requires the SAME state/labels/authorization, but a DIFFERENT question fingerprint. The Von fixture parser also independently checks that the actual options were reordered.
- Digests are matching identifiers, not signatures or physical runtime evidence. Since the scorer cannot reconstruct inputs from hashes alone, the trusted producer must archive the canonical, SANITIZED question-render contract separately and verify its digest.
- This is an intentional draft-only schema change, with no impact on any production router routes, streams or request receipts. Older experimental JSONL observations without question_sha256 are refused rather than silently considered comparable.
- QSIV does not yet have a real inference adapter. When that exists, it must hash the same model-visible typed contract before emitting comparable observations; matching only the shared document is insufficient.
