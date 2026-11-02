# Auto-Router SQLite token provenance migration

Status: **Draft / no production deployment**. Stacked on token-accounting
fix PR #108. Version 2026-11-02.

## Problem

The existing router's retained SQLite rows were written before usage
provenance was known. Some token counts came from quota estimates,
including failed embedding jobs. Those historical rows cannot be
retroactively authenticated as actual model generation.

The preceding fix records provider-returned token counts instead of
quota estimates for new requests, but a naked integer 0 for missing
usage is not evidence of zero generation. The schema needs an explicit
provenance label to avoid combining unrelated populations.

## Forward-only change

On database initialization, add a token_usage_source TEXT column to
usage_events and runtime_samples if missing. The column is additive,
NOT NULL, and defaults to legacy_unverified. This **does not overwrite,
delete, normalize or backfill** any old counters.

New writes use only:
- provider_reported: valid upstream usage metadata supplied by provider
- unknown: usage absent, invalid or not attributable to a provider

The writer rejects attempts to designate new records legacy_unverified
or use an arbitrary new source label.

The public ledger summary now counts prompt/output/total tokens only
from provider_reported usage rows. Requests still count all records
for operational rate reporting. For transparency, totals also include
measured_requests, unknown_usage_requests and
legacy_unverified_requests. Per-provider total token counts have the
same measured-only constraint.

Historical runtime samples with unknown or legacy usage retain their raw
metadata for investigation but **cannot influence** throughput averages,
value-per-second or model-routing counterfactual predictions. Absent
verified throughput appears as NULL, not a fictitious zero. The legacy
row may retain an old throughput integer but is never presented as a
verified average.

Provider-reported usage on a failed HTTP request can represent real
provider consumption and therefore remains counted in observed tokens.
Successful-generation figures must be explicitly status-filtered;
this ledger summary is a provider-reported usage sum, not a success-only
generation or a GPU utilization metric.

## Limits and release gates

The SQLite ledger prunes to 5,000 recent usage and runtime rows, so
the summary represents RETAINED LOCAL ROWS, never a complete fleet-wide
24-hour total. The summary explicitly advertises
window=retained_sqlite_rows_not_fleet_24h.

No trace content, raw prompts, or model output are stored by this change.
No production graph authority or signed model admission paths are touched.
No stale upstream models or free-provider routes are enabled. No new
inference or paid hosted API request occurs.

Before production migration: review database location/storage budget and
current backup, verify rollback procedure, run migration on a **copy** of
the actual schema, prove independent CI and retention limits, and
inspect any downstream consumers of old raw usage counters. SQLite
ALTER TABLE is additive and idempotent but schema rollback would
require a separately planned table rebuild; do NOT execute the
migration on the live database as part of code review.

## Local synthetic test matrix

- Fresh usage rows labeled reported vs unknown; true reported zero
- Existing old schema before both columns; additive migration
- Old 201,200-token failed embedding quota remains legacy_unverified,
  not measured, and its old throughput cannot rank a provider
- Startup migration is idempotent and preserves raw rows
- New provider-reported usage is counted after historical migration
- Counterfactual value from unknown usage cannot be promoted
- Invalid writer provenance rejected
- Runtime and usage readbacks retain provenance field
- Provider-reported consumption on unsuccessful HTTP response distinct
  from a successful completion.

No production SQLite database was opened or modified for these tests.

## Read-only live-schema rollback rehearsal (Nov 2, 2026)

New review-only script:

    python3 scripts/audit-ledger-provenance-rollback.py --source-db /path/to/sqlite.db

The auditor opens the source SQLite database with URI mode=ro and
PRAGMA query_only=ON. It rejects symlink input, oversize files and
insufficient available memory before connecting. It creates two
anonymous in-memory backups, with no on-disk backup or source writes.
One is an unchanged baseline snapshot. The second is migrated with
the additive DDL, checked, migrated again (idempotent no-op), then
restored from the baseline in-memory snapshot.

This is a restore REHEARSAL, not an operational production rollback.
The actual rollback must be coordinated with traffic fencing and a
durable, independently verified pre-migration SQLite backup that
includes committed WAL contents. Restoring a previous snapshot may
lose writes committed afterward. Do not automatically roll back
a live database or attempt an in-place column drop.

On the real running git-router-1 database, the audit piped its
script into Python inside the existing container. It did not create
a new container, deployment, disk snapshot, model invocation or
router write. The database was 1,782,984,704 bytes; both relevant
tables held 5,000 rows and two indexes. All nine checks passed:

- New columns present on migrated in-memory candidate
- All original rows conservatively labeled legacy_unverified
- Original ID/input/output/total token tuples preserved
- Schema changed only in the migrated candidate
- Reapplying migration was a no-op
- Restore fully recovered original schema and counters
- Restore removed the added columns
- Read-only source connection confirmed
- Source schema and counter fingerprints unchanged

Seven isolated tests also check WAL snapshot contents, immutability, unrelated records, source
size/memory guards, symlink rejection, already-migrated schema
and invalid table-name rejection.

No token values were printed or persisted to audit outputs.
A successful in-memory rehearsal is not a production backup
and cannot establish fleet-wide actual generated-token totals.
