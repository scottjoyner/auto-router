# Runtime throughput must be fresh, observed, and plausible

Date: November 2, 2026. Research-only source patch, NOT deployed.

## Incident

The current production Auto-Router /metrics/ops endpoint advertises
approximately 523,000 tokens/second for lmstudio-xwing. That value
is not credible current single-node decode throughput. Its historical
SQLite database has NO usage_events or runtime_samples in the
last 24 hours; retained rows end September 10, 2026.
Several old rows have output_tokens=0 and enormous
tokens_per_second values caused by tiny elapsed durations.

The existing recent_runtime_samples query ordered by newest ID
but did not filter by age. Its runtime_summary and the swarm
dashboard then reported the stale values as live averages.

## Patch behavior

UsageLedger.recent_runtime_samples now selects ONLY rows
created within the last 24h, allowing 60 seconds of clock skew.
For each row, throughput_valid is true only if:
- the recorded execution outcome is successful;
- output_tokens is positive;
- elapsed wall time is at least 10 ms;
- recorded generation rate is finite, positive and
  does not exceed 10,000 tokens/second.

The 10,000 threshold is an upper sanity bound for per-request
rates, NOT a benchmark-based node capacity estimate.
Rows failing those conditions remain visible as outcomes,
but their token/sec and value/sec fields are null.
Runtime summary exposes throughput_observed_samples
independently from total attempts. Node/provider rankings
exclude records explicitly marked throughput_valid=false.

A zero-sample fresh window means NO CURRENT MEASUREMENT.
It does not establish no other inference occurred.
The entire retained SQLite database is NOT rewritten,
deleted, migrated, or truncated by this patch.

## Verification

In the isolated worktree:

    PYTHONPATH=src /home/scott/git/auto-router/.venv/bin/python \
      -m pytest -q tests/test_ledger.py \
      tests/test_ops_dashboard_routes.py \
      tests/test_backlog_scheduler.py tests/test_backlog_routes.py

18 tests passed, 1 intentionally skipped.
New regression cases exercise:
- historical and future timestamps excluded from the
  current 24-hour window;
- zero-output samples with 523,720 tok/s rejected;
- even positive-output implausible 100,000 tok/s rejected;
- a fresh legitimate 50 tok/s sample remains admissible.

## Deployment and utilization implications

This patch has NOT been deployed to Auto-Router; production
health and /metrics/ops still reflect the previous code.
A separate CI/deployment review is required, preserving
API compatibility, rollback and free-only provider controls.

Fresh, trusted throughput evidence is a prerequisite for
adaptive admission and backlog scheduling. Do not maximize
a fabricated gauge. In particular, do not lift physical
fleet admission gates or trigger paid provider fallback
because historical rates look impressive.
