# Two-eligible Von CPU decision-value test — 2026-10-11

**Status: 48 genuine Von 1.3 CPU requests on fictional metrics; zero live routing, real admissions, independent labels, or signed execution receipts.**

Stacked on [policy-value draft PR #47](https://github.com/scottjoyner/auto-router/pull/47), [physical CPU draft PR #46](https://github.com/scottjoyner/auto-router/pull/46), and [source-only scorer draft PR #44](https://github.com/scottjoyner/auto-router/pull/44). Tracking [issue #43](https://github.com/scottjoyner/auto-router/issues/43).

## Why this experiment exists

The preceding 100-response report used only 10 invented situations with **zero or one eligible node**. In that setting, a deterministic singleton-or-defer selector succeeds by construction, so it cannot test ranking between multiple eligible providers.

This experiment deliberately supplies **TWO simultaneously eligible fictional nodes** in every case. It does NOT establish real authorization: admissibility is stipulated by the handcrafted generator and has not been obtained from authenticated AssistX, PostgreSQL or signed process witnesses.

We define an objective **independently of Von** and compute its exact optimum:

- `expected_completion_ms = TTFT_ms + (output_tokens / generation_tokens_per_second) * 1000`
- `estimated_incremental_energy_J = watts * (TTFT_ms / 1000 + output_tokens / generation_tokens_per_second)`

All values and devices are fictional. There is no model-quality uncertainty, real queueing, cold-start penalty, thermal state, R9700 GPU performance, network latency, power telemetry, energy measurement or future outage survival component.

The primitive deterministic heuristic ranks by highest tokens/sec for latency tasks or lowest watts for energy tasks. The exact arithmetic oracle uses **both** TTFT and throughput (and watts for energy). Comparing Von to both shows whether it adds value over a crude heuristic and whether either approaches the optimal mathematical selector. When all these numbers are known and trustworthy, a model cannot beat the exact arithmetic objective; this benchmark is a *screen for failure*, not proof that generative guidance is pointless for uncertain data.

## Registered matched design and physical execution

- `experiments/von_multi_eligible_cpu.py`: 12 balanced, fixed invented profiles, with 6 independent source cases preferring A and 6 preferring B. Six cases optimize time; six optimize energy.
- Each source case was asked in two **distinct** formats: **raw** (TTFT, tokens/sec, watts) and **derived** (an independently computed latency or joule estimate), each with both A/B display orders: **12 × 2 × 2 = 48 real CPU forward passes**.
- Exact profile metrics and both potential costs are versioned in source, while each observation carries state, ordered-question and source-fact digests. Source runner originated at commit `a4e34dd4a08f1d13655b7e86b139c56bb913544f`; later changes add tests, reconciler and documentation, not a second physical run.
- xwing CPU execution reused locally cached Von 1.3 with `wfzyx/von` Hugging Face SHA `498ceba33390b32cfefaab6422ec380318ba9b99`. Model backbone SHA256 `af57d5d2ab15715a753a1eb4add4271d1aecce7e76f3365f629c082df329297a`; scoring head SHA256 `3faf27f88d30aaf9aa37860d4cdef99f1d05450cf40364f6236ac892d4d139ed`.
- Admission preflight on xwing: AC powered, ~17.17 GiB RAM available, ~84.72 GiB disk available, load ~5.96 across 24 logical CPUs. Run was contained in `systemd-run --user --scope` 8 GiB max RAM and 150% CPU quota, `nice 19`, hard timeout, no chains, overflow refused, CPU only, HF offline, CUDA/HIP invisible. The source enforces resource-scope bounds before loading the checkpoint.
- **No model service started, no NAS write, no live fleet requests or route changes**. The model cache already existed from PR #46; no new weights downloaded.

## Pinned data provenance and audit

- Genuine xwing report: `/tmp/von-cpu-pilot-e0GNTh/von-multieligible-objective-20261011.json` — **33,521 bytes**, SHA256 **`216cdb6753ac87f6d9197435d27db24f2971ca0b390948617197f94234313423`**. Retained on xwing in a private temporary directory; never added as a raw report to GitHub or NAS.
- `experiments/von_multi_eligible_reconcile.py` runs **without Von or a network call**, first requiring the exact report SHA256, then comparing all 48 responses to the registered fictional profiles, labels, costs, source/question fingerprints, exact weight hashes and aggregate totals. Unknown raw prompt fields, invalid confidences, absent/duplicate cases, fake execution attestation or changed records are rejected.
- **Unit-correct, metadata-only GitHub artifact:** [experiments/evidence/von-multieligible-objective-20261011.json](../experiments/evidence/von-multieligible-objective-20261011.json), SHA256 **`f22b1a659eaafc5115590e88040e778c0d4d0a38c6548f79e7e383dd7d182285`**, 3,516 bytes. The exact published bytes were retrieved from xwing, where the audit ran.
- **Important audit correction:** the original 48-call runner's `summary.model_total_objective_regret` sums milliseconds **and joules** and is dimensionally invalid. It remains in the original, pinned, immutable research report only for reproducibility and must **never be cited as a cost or efficiency statistic**. The verified audit file reports regret **separately** for `latency_ms` and `energy_j`, with no combined sum. Energy consumed by Von's own CPU inference was **not** measured; no net-energy improvement is claimed.

## Genuine model outcomes (12 responses per objective/template, 6 unique source profiles per row)

| Criterion | Von selected mathematically best | Crude deterministic heuristic | Exact arithmetic policy |
|---|---:|---:|---:|
| Raw latency measurements | **6/12** | 12/12 | 12/12 |
| Precomputed latency estimates | **8/12** | 12/12 | 12/12 |
| Raw estimated energy inputs | **6/12** | 10/12 | 12/12 |
| Precomputed estimated energy | **6/12** | 10/12 | 12/12 |

**Model collapse on raw inputs:** Von chose `node_b` for **every one of the 24 raw-input requests**, despite a deliberately balanced correct-label set. With derived numbers it picked A occasionally (2/12 for latency, 4/12 for energy) but still underperformed the simple heuristic. Prediction did not change across A/B display-order pairs (0/24), which in this case largely reflects a persistent preference rather than strong semantic reasoning.

### Regret, *not mixed across physical units*

| Template | Objective | Von regret sum | Primitive heuristic regret sum |
|---|---|---:|---:|
| Raw | milliseconds | 6,143.200 ms | 0.000 ms |
| Derived | milliseconds | 1,703.200 ms | 0.000 ms |
| Raw | joules | 270.939 J | 33.000 J |
| Derived | joules | 300.339 J | 33.000 J |

These sums count correlated display-order duplicates. Do not treat 12 requests as 12 unique physical observations. On latency tasks, the audit explicitly subtracts Von's own decision time from its improvement over the simple heuristic:

- **Raw latency:** approximately **−15,485 ms** total (12 variant requests).
- **Derived latency:** approximately **−8,827 ms** total (12 variant requests).

The negative sign means Von increased the objective-relative estimated completion time plus decision overhead. An energy-total calculation that includes CPU inference is **not possible** without a power/energy measurement.

## Limits and decision

- These are **12 unique fictional scenarios**, 48 correlated prompt/order variants, 0 real operational cases and 0 separately authenticated node-admissibility proofs. They cannot establish confidence intervals for any particular physical provider, iPhone experience, reliability of a loaded state, or genuine quality optimization.
- Von did successfully execute, but performed poorly for synthetic numerical ranking on this hardware. Even precomputed objectives did not make it a reliable scorer, and probabilities were not calibrated for this choice domain.
- Deterministic arithmetic is **provably optimal for the stated, complete, invented objectives**, not necessarily for real task quality or partially observed execution environments. We have not assessed situations with unknown quality, competing nonnumeric reliability constraints, or dynamic outages. The original zero/singleton baseline and this exact-metric two-eligible baseline should be treated as two different, narrow evidence boundaries.
- **Operational design decision:** never make Von/QSIV a second admission authority or a substitute for arithmetic. For two authenticated and independently admissible nodes, compute a transparent score from trusted TTFT, throughput, predicted task cost, power and quality models with explicit uncertainty, and compare that cost-based heuristic against any advisory LLM shadow before permitting further rollout.
- **Remaining gates:** ≥100 independently reviewed *real* matched decisions, actual authenticated multi-eligible runtime state, age/health/lease freshness, source-authoritative owner binding and independently attested execution receipts from issue #37, quality and power measurements, OOD/dynamic-node tests, and operator approval. QSIV Blackwell FP8/AMD portability remains separate and unverified.

**No deployment, release, admission change or NAS ingestion is authorized by this study. Keep every PR in the stack draft-only.**
