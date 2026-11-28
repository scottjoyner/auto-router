# Router outbox freshness/401 witness — nonpromoting

This script addresses the historical event-delivery evidence noted in
[Auto-Router #102](https://github.com/scottjoyner/auto-router/issues/102).
It **does not repair delivery**, make an authenticated request, or authorize
deployment. Legacy dead-letter records and credential custody stay untouched.

## Purpose

The x1-370 production SQLite outbox had tens of thousands of historical
`dead_letter` rows, mostly HTTP 401 classifications from July–September
2026. Merely finding zero `pending`/ `retry` rows could be mistaken for
healthy current delivery when no new events have been delivered.

`scripts/router_event_outbox_freshness.py` connects to an explicitly
named **absolute SQLite database path** in `mode=ro`, then sets
`PRAGMA query_only=ON`. Only static aggregate queries run. The script
never fetches or prints event payloads, last-error strings, event IDs,
source services, idempotency keys, bearer headers or token values.

Output includes counts by status, new `dead_letter` counts since a
specified Unix cutoff, counts matching a fixed HTTP 401 error prefix,
and the timestamp of the last locally recorded `delivered` row.

## Fail-closed meanings

- `HOLD / new_auth_denied_dead_letters`: new HTTP 401-classified failures
- `HOLD / new_dead_letters`: new non-401 dead-letter rows
- `HOLD / pending_or_retry_not_drained`: unresolved queued/retry rows
- `HOLD / no_recent_delivery_proof`: no locally recorded delivery within
  the configured age threshold, even when there are no new failures
- `HOLD / invalid_or_future_record_timestamp`: the database's timestamp
  cannot be trusted as a valid witness
- `HOLD` for path, symlink, schema, SQLite access and unknown statuses

An apparent pass is only `FRESHNESS_OBSERVED_ONLY`, exit 0, **never**
authenticated delivery proof or deployment authorization. It cannot
establish that the remote AssistX sink accepted a legitimate, authorized
new event. The timestamp cutoff and freshness threshold are operator
inputs and require independent clock trust.

Example **read-only** invocation inside the existing router Python
environment, after selecting a trusted cutoff and resolving the actual
database path using a separate approved process:

```sh
python3 -B scripts/router_event_outbox_freshness.py \
  --database-path /data/router.sqlite3 \
  --since-unix <UNIX_UTC_CUTOFF> \
  --freshness-seconds 86400
```

For physical production comparison, the checker can be streamed over
stdin with `docker exec -i ... python3 -B -` rather than installed
into the older container. This does not start a router worker.

## Recorded physical witness

On October 25 (local time), the exact PR-source witness passed
19/19 offline SQLite tests independently on x1-370 and Xwing. Against
the running `git-router-1` DB in read-only mode with
`since=2026-10-25T00:00:00Z`, it returned **HOLD,
no_recent_delivery_proof, exit 2**: no new dead letters/401s, but last
delivered event dated September 10. It disclosed no event payloads or
raw historical errors. This is an historical-freshness warning; it is
not evidence that the current remote AssistX sink is down.

The existing source-only CI/deployment checks and runtime reconciliation
from #102 remain necessary. Do not requeue 57k historical dead letters,
resurrect raw archive harvesters, clear database statuses, rotate
credentials or start production services based solely on this witness.
