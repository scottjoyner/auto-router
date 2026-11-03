# Outbox pressure truthfulness: scheduled drain versus delivery failure

## November 3, 2026 read-only physical evidence

Production `git-router-1` still reports degraded, zero enabled free hosted
providers and an independently unresolved operator-admin authentication issue.
Its local `/health` outbox fields repeatedly showed *a bounded queue of new
events* approaching the five-minute dispatch, despite `last_outcome=success`,
no retry or dead-letter events, and the next dispatch being on schedule.
One live sample had `pending=50`, `retry=0`, `dead_letter=0`, prior successful
dispatch 142 seconds earlier and next dispatch about 157 seconds away; the
current classifier called this `warning`. A later observed successful cycle
drained the count to eight. The events are monitoring/outbox records, **not
prompt tokens, model generations, accepted AssistX Tasks, or evidence of
provider throughput**.

## Narrow source-only fix

`build_outbox_pressure_status` now uses the *live event outbox summary* when
there is an event outbox, and only uses `last_summary` when there is no live
outbox (e.g. a legacy status-only fixture). The response explicitly publishes
`pressure_count_source` as `live_outbox` or `last_dispatch_snapshot`; it does
not resurrect stale previous-delivery rows when the live queue is empty.

A bounded, expected scheduled queue is `level=ok`, with
`scheduled_drain_expected=true`, **only if all** of these are true:

- live outbox exists, pending positive, retry and dead-letter counts zero;
- pending at most the conservative expected-batch cap (four times warning
  threshold, always below critical threshold);
- dispatch is configured and enabled, prior outcome `success`, not a dry-run;
- valid positive dispatch interval; last completion no older than interval
  plus 30 seconds; next run due within the interval;
- the critical threshold has not been reached.

Any overdue, failed, disabled, unverified, retried, dead-letter or larger
backlog retains warning/critical classification. `pressure_total`, pending,
retry and dead-letter counts remain visible even while the level is nominal.
The code never triggers dispatch, delivers events, changes the scheduler,
modifies authorization, sends credentials or creates any inference request.

## Validation and release boundary

Local **27 tests passed** across outbox storage, event delivery, service
routes, main health and ops-dashboard views; focused negatives include stale
`last_summary` fallthrough, absent live outbox, no deadline, failed dispatch,
dry run, disabled sink, retry/dead-letter, large backlog and critical pressure.
Blocking Ruff checks, compilation and `git diff --check` pass.

A read-only call to live router health fed only the allowlisted dispatch
metadata into this new *local* classifier: runtime `warning` for 50 pending
became candidate `ok/scheduled_drain_expected=true` with identical raw queue
count. This is source validation, not deployed behavior.

**Do not merge/cut over without** exact-head hosted CI, full consumer/metrics
contract review, and independent security release of anonymous Tailnet
`/admin` endpoints. The canonical AssistX signed work projection remains
expired, Task slots zero, and node-agent authentication still fails; this
telemetry correction authorizes **no additional model workloads**.
