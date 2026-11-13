# Joyner R9700 LFM3: offline token-budget diagnostic (research only)

## Decision record — November 13, 2026

**Scope:** Source-only analyzer for already persisted local synthetic probe receipts.
Read-only offline execution, no model requests, no API key access, no new
service, GPU allocation, config edit, training restart, routing, or deployment.
The existing Joyner inference/benchmark and xwing training recovery gates remain HOLD.

**Motivation:** In 48 batches recorded on Joyner across the last 24 hours,
exact 5/8 repeated. Arithmetic and boundary returned SHA-256 of empty text
and consumed the 64-token cap in every batch; successor had a nonempty
mismatch. The first two are **possible** truncations, NOT proven absent
`finish_reason` / reasoning-token data. All eight prompts are repeated
synthetic tests, not independent heldout validation.

Run with an explicit cap matching the original probe source (currently 64):

```sh
python3 scripts/joyner_lfm3_probe_diagnostics.py \
  /path/to/online-probes.jsonl --last-batches 48 --completion-cap 64
```

The script emits only aggregate counts and per-case categories to stdout.
It refuses unknown schemas, mixed models, duplicate/missing cases and
malformed receipts. Do not copy private traces, raw responses, credentials
or protected model files into the repository. Do not infer production quality.

**Follow-up acceptance:** Under separately approved GPU/lease and resource
gates, add `finish_reason` and numeric reasoning-token telemetry to future
probe receipts; then compare bounded output budgets and heldout non-repeated
cases against a deterministic baseline. Never reinterpret an old receipt
as proof of truncation without the missing telemetry.

**Rollback:** No deployed component has changed. Remove this isolated source
branch if rejected; its parser does not modify receipt files.