# Matched Von CPU wording and abstention experiments — xwing — 2026-10-11

**Status: actual offline CPU model execution on fictional cases, NOT fleet routing acceptance.** This report extends [draft PR #46](https://github.com/scottjoyner/auto-router/pull/46) and [issue #43](https://github.com/scottjoyner/auto-router/issues/43). All results below come from the existing pinned Von 1.3.0 model on xwing. No real user requests, credentials, location data, or live fleet observations were used.

## Common execution envelope

- Machine: xwing, Ryzen AI MAX PRO 390, 24 logical CPUs; powered from AC; dynamic admission passed with ~17–18 GiB available RAM, ~84 GiB disk free and moderate load.
- Pinned model: Hugging Face `wfzyx/von` revision `498ceba33390b32cfefaab6422ec380318ba9b99`. Backbone SHA256 `af57d5d2ab15715a753a1eb4add4271d1aecce7e76f3365f629c082df329297a`; scoring-head SHA256 `3faf27f88d30aaf9aa37860d4cdef99f1d05450cf40364f6236ac892d4d139ed`. The existing cached six-component snapshot was SHA256-verified before each run.
- `von-sdk==1.3.7` isolated under xwing `/tmp/von-cpu-pilot-e0GNTh/packages`; offline during inference, no chains, no silent overflow, CPU only, no CUDA/HIP visibility, low priority and external timeout.
- Both subprocesses used `systemd-run --user --scope -p MemoryMax=8G -p CPUQuota=150%`. A separate live cgroup-v2 probe reported `memory.max=8589934592`, `cpu.max=150000 100000`. This is a resource-bound research execution, NOT a signed execution receipt.
- No model service, scheduler, admission rule, auto-router or AssistX endpoint changed. No NAS writes; reports are 35 KB and 23 KB, retained in the private temporary workspace. A follow-up check found no lingering Von process.

## Trial C: 60 genuinely evaluated matched wording/option-order prompts

Generator: `experiments/von_matched_wording.py`, source first introduced at commit `729c14a0d2f9181a05f1a9ec02fa70ca6bf7b429`. Ten distinct **fictional** fact patterns: offline, lease, loaded vs merely available, owner-binding (each with A and B as the independently authorized side), plus two missing-authority cases. Each is posed as three instruction/option styles (terse, explicit eligibility, strict authority) under both A/B orders: **10 × 3 × 2 = 60 actual CPU forward passes**. State SHA256 and mechanical gold are identical within each six-variant family. The option display order changes the question-contract SHA256 but not authority.

Stored report: xwing `/tmp/von-cpu-pilot-e0GNTh/von-matched-wording-20261011.json`. SHA256 **`18b70de2c4dac8b264d53dd4234b3600dd8df6f7b1dc6bbcf2a0b6d240f31c7f`**, size 35,007 bytes. No raw input state is stored in the report; fictional state templates are versioned in the source.

| Question format | Correct eligible routes | Deferred despite eligible | Safe no-route on missing proof | Unauthorized recommendation on missing |
|---|---:|---:|---:|---:|
| Terse | 2/16 | 14/16 | 2/4 | 2/4 |
| Explicit current eligibility | 0/16 | 16/16 | 4/4 | 0/4 |
| Strict authority | 0/16 | 16/16 | 4/4 | 0/4 |

- 0 changed predictions across 30 A/B option-order pairs, **not** a general invariance proof: only 10 unique fact patterns and a model heavily favoring defer.
- Both correct terse recommendations were for the SAME underlying fictional owner-binding fact pattern, with two orders. They do not represent two independent correct scenarios.
- Explicit and authority templates selected `defer` on **all 20** requests in their respective styles. A predominantly abstaining policy can make a safety metric look positive while providing almost no routing utility.
- Median measured decision latencies: terse ~498 ms, explicit ~736 ms, authority ~716 ms. These are in-scope measured latencies, not general fleet p99 or high-concurrency throughput.

## Trial D: 40 matched **two-option vs three-option** forward passes

Generator: `experiments/von_abstention_ablation.py` introduced in commit `6023ec2794415f5cb671f259f9d8d855527ab351`. From the explicit variant above, each of **20** scenario/order contexts is run twice with identical underlying state, instructions, A/B descriptions, gold and order. The **sole** changed model-visible feature is the addition/removal of third Choice option `defer`. There are 16 paired actionable contexts (8 unique factual scenarios ×2 orders) and four paired contexts lacking authority (2 unique scenarios ×2 orders).

Stored report: xwing `/tmp/von-cpu-pilot-e0GNTh/von-defer-option-ablation-20261011.json`. SHA256 **`4a3a42bee2beaa8b4fede40c7564c6aeccb9817627776e6f9daf59a336f57028`**, size 22,645 bytes.

| Outcome | A/B forced-choice | A/B/Defer allowed |
|---|---:|---:|
| Correct authorized route, evidence complete | **12/16** | **0/16** |
| Wrong route, evidence complete | 4/16 | 0/16 |
| Abstention despite a good route | N/A | **16/16** |
| Safe defer with missing evidence | N/A | **4/4** |
| Unsafe route suggestion with missing evidence | **4/4** | 0/4 |
| Mean selected-option probability (all 20 per condition) | ~0.552 | ~0.440 |
| Median decision time | ~697 ms | ~762 ms |

- Within the 16 actionable paired contexts, **12/16** switched from a correct A/B route to `defer` on adding that answer. No paired actionable case retained the same runtime decision.
- Two-choice per-family correctness: **lease 4/4, offline 4/4, owner-binding 4/4, LOADED vs AVAILABLE 0/4**. These counts are correlated by position/order and come from only eight unique source situations.
- Missing-authority cases under forced choice selected an actual runtime all 4 times because `defer` was not an offered answer. When `defer` was present all 4 deferred.
- The experiment isolates an **option-set effect** much more tightly than trials A/B/C. It still does NOT identify a general causal mechanism inside Von or justify a statistical independence assumption (16 paired items correspond to just 8 unique scenarios).
- Routing and owner authorization remain deterministic and separate, including on the two-choice correct cases. A model answer cannot create signed lease, physical loaded state, authenticated owner, or capacity.

## Decision / recommended architecture

**Keep Von as a diagnostic or advisory classifier, not a route selector or fallback authority.** In this observed setting, forced-choice has a measurable unsafe-recommendation problem; adding an unconditional abstention choice collapses coverage. The correct way to allow deferral is deterministic eligibility: obtain a fresh authenticated allowed set, return `NO_ROUTE` immediately if empty, and only use model ranking or summarization as a *shadow* inside an independently eligible set. Even with one eligible node, a model may be unnecessary; the deterministic selector already has the answer. An LLM may explain or summarize the why, but must never mint admissibility or operator approvals.

This conclusion applies to these small fictional probes, not to general Von reliability across other domains. Future experiments should compare against a trivial policy baseline and a registered scoring heuristic on at least 100 independently reviewed real/held-out scenarios, with request ownership and physical execution receipts, before any wider adoption. Need an independent load and energy study if the model is considered for advisory summaries.

**Explicit no-go:** no production routing, no merge/release, no trusting >0.8 confidence as authorization, no claims of QSIV/ROCm compatibility, no ingestion of raw telemetry, and no NAS backup until provenance/storage policy are reviewed. Keep [PR #44](https://github.com/scottjoyner/auto-router/pull/44) green/draft and [PR #46](https://github.com/scottjoyner/auto-router/pull/46) stacked/draft.

## Reproducibility

Run source-only checks with Python standard library:

    python3 -m unittest discover -s tests -p 'test_von_*py' -q
    ruff check experiments/von_matched_wording.py experiments/von_abstention_ablation.py

Dry runs, default no inference:

    python3 experiments/von_matched_wording.py --workspace /tmp/isolated-pilot
    python3 experiments/von_abstention_ablation.py --workspace /tmp/isolated-pilot

Real model execution requires a separately admitted xwing environment with pinned file checksums and readable cgroup-v2 MemoryMax<=8GiB and CPUQuota<=1.5 CPUs. Explicit --execute, --ack-cpu-impact and a new path under the private /tmp workspace are required. The underlying program never routes, grants permissions, calls a remote provider, or modifies NAS.
