# Passive Joyner R9700 GPU/receipt correlation

**Research-only, source-only, no deployment.** This optional script reads
`/proc/<pid>/stat`, `/proc/<pid>/cgroup`, GPU client's DRM fdinfo and an
already-written LFM3 synthetic-probe receipt. It never sends HTTP requests,
reads key files, starts models, changes processes or modifies the receipts.

Example for a known authorized running LFM3 service PID:

```sh
python3 scripts/joyner_lfm3_passive_gpu_observer.py \
  --pid PID --receipts /home/scott/lfm3-research/continuous/online-probes.jsonl \
  --wait-seconds 45
```

To capture coincident work, run inside the next **existing** 30-minute probe
window; don't force a new probe or start a new GPU process. If no new receipt
arrives during the sample, correlation is **inconclusive**. Even positive
counter deltas and new receipts cannot prove exact per-token GPU attribution:
unobserved clients can send concurrent requests. Only a separately approved
controlled canary can establish that.

Checks: same process start ticks, expected service cgroup, expected discrete
R9700 PCI BDF, deduplicated DRM client IDs, non-regressing compute counters,
expected receipt schema/model and completed 8/8 HTTP requests. No identifiers,
raw prompts, completions, API keys or network addresses are persisted.

The output is an aggregate/boolean JSON **HOLD** witness; every missing,
malformed, unauthorized or changing input fails closed. Keep the existing
inference service, xwing admission hold and production routing unchanged.

**Timing qualification (2026-11-13):** The existing systemd timer has
`AccuracyUSec=30s` and `RandomizedDelayUSec=10s`. A passive 20-second window
starting 9 seconds before nominal 17:05 fired before the actual 17:05:20
service start and therefore correctly returned `inconclusive`. The default
45-second duration and hard maximum of 60 seconds cover normal timer jitter
without altering or triggering that timer. Collect a post-run receipt and
engine counter within the same bounded interval; never infer GPU causality
from a nominal calendar trigger alone.
