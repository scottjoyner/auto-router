# Von CPU research checkpoint — xwing — 2026-10-11

Status: REAL model inference on a fleet CPU under an isolated research scope; all labels and states were fictional. This is NOT independent operational acceptance, signed runtime execution attestation, or approval to route traffic.

## Runtime and file-backed provenance

- Node: xwing, AMD Ryzen AI MAX PRO 390, 24 logical CPUs; mains power connected. Admission checks showed approximately 17–18 GiB available RAM, 82–87 GiB available root storage, and short load ~2–5.
- Python: 3.12. Existing PyTorch 2.13.0+cu130 was used on CPU; Transformers 5.13.0.
- Von SDK wheel: 1.3.7, SHA-256 c728895955da163eff7aebe4cd0387593ec0c2af6eec8ea5e18399236366eeef. Installed into the temporary pilot workspace, not system Python.
- Model: wfzyx/von, pinned Hub revision 498ceba33390b32cfefaab6422ec380318ba9b99.
- Model backbone SHA-256: af57d5d2ab15715a753a1eb4add4271d1aecce7e76f3365f629c082df329297a.
- Scoring head SHA-256: 3faf27f88d30aaf9aa37860d4cdef99f1d05450cf40364f6236ac892d4d139ed.
- Calibration SHA-256: 9b32949dcd0cfd122db509c9bd5be67f0cfe35696153aa93d667caf0aae147c9.
- Pinned HF cache: approximately 3,164,054,266 bytes, inside a private /tmp workspace on xwing; NO NAS write. All six required components were present and verified before inference.
- CPU environment: VON_CHAINS_DIR=off, VON_ON_OVERFLOW=refuse, VON_DEVICE=cpu, HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1; no CUDA or HIP devices exposed.
- Process constraints: systemd-run user scope MemoryMax=8 GiB, CPUQuota=150%, nice 19 and external timeout. A separate process-control probe verified cgroup-v2 memory.max=8589934592 and cpu.max=150000 100000.
- There were no router configuration, admission, background-producer, permissions or model-dispatch changes; no Von process lingered after the trials.
- This is operator-authenticated remote-shell process evidence, NOT an independently signed execution witness. Peak observed memory and energy were not measured.

## Trial A: first four typed-choice observations

Input is the repository's four-row entirely fictional fixture, SHA-256 b52683ae6b3205e71871c964e619910974e8a5016d0ed0fb0d256400dbe50742. It contains two independently mechanized actionable original cases, one option permutation, and one case without authority evidence.

- Four actual CPU inference responses were obtained; trained scoring head and input-conditioned calibration loaded from the exact checkpoint.
- Among the two actionable original cases, correct = 0/2. Across four total variants, all four output suggestions were ineligible or ungrounded; a model choice was NEVER forwarded to an actual node.
- The one option permutation did not change its model prediction; n=1 is too small for a stability conclusion.
- Latency among the TWO original actionable examples: p50=1060.499 ms, p95=1558.955 ms, p99=1603.262 ms. Percentiles based on n=2 are not credible for deployment capacity planning.
- Result explicitly declared execution_attested=false and safe_for_production=false. Labels are fictional, not independently sourced operational observations.

## Trial B: eight simple controls with explicit descriptions

The complete fictional states, question templates, and expected labels are versioned in experiments/von_cpu_controls.py. The results below are genuine inference on xwing, but **the labels are handcrafted**.

| Case | Family | Result | Selected probability | CPU decision time |
|---|---|---|---:|---:|
| cls_refund | General | correct | 0.987 | 1266.037 ms |
| cls_outage | General | correct | 0.985 | 300.570 ms |
| cls_account | General | correct | 0.981 | 309.683 ms |
| cls_delivery | General | correct | 0.984 | 305.307 ms |
| route_a | Routing | correct | 0.689 | 363.397 ms |
| route_b | Routing | correct | 0.706 | 341.128 ms |
| route_lease | Routing | correct | 0.684 | 354.375 ms |
| route_loaded | Routing | correct | 0.559 | 341.597 ms |

General correct = 4/4, routing correct = 4/4. The first item includes initial warmup; the rest are not independent or large enough to estimate fleet p99.

Aggregate report SHA-256 219db0081cd6d22fc06f202fbcc1b7e38b39d35bd3135ef3bae7d4dd3293b720. Output held only in xwing's private temporary directory. No raw private fleet data was used.

## Interpretation: large prompt-design sensitivity, no promotion

The same pinned Von model performed very differently on the two *different*, tiny sets: 0/2 for the initial ambiguous/hostile routing tests versus 4/4 routing controls with richer option descriptions. These are not matched prompts, so they do not isolate the cause or prove general accuracy. They do show why the exact question template and ordered option descriptions must be registered and hashed, and why options shown to a model can never automatically be treated as authorized.

Generic service-request confidence was ~0.98; even correct routing controls had confidence 0.56–0.71. Never reuse a generic confidence threshold for routing admission without domain calibration and independent held-out labels.

NO-GO remains: at least 100 independently reviewed, held-out matched fleet cases, explicit missing-evidence and expiry tests, independent authenticated request owner and physical execution receipts, measured memory/energy/p95/p99 under real conditions, and explicit operator approval. No route may be admitted based solely on a model's answer.

QSIV is still a distinct portability effort: its fast FP8 path is Blackwell-targeted, and BF16/ROCm support on the AMD fleet remains unverified. x1-370 ROCm command-line preflight had HSA and missing-toolchain errors; this CPU trial made no GPU driver changes.

The pinned 3.16 GB cache remains in xwing's temporary workspace for review/replay; no background process remains. Do not remove it without verifying its owner and preserving aggregate evidence. This physical CPU run is a separate stacked draft research slice, not grounds to merge or promote PR #44.
