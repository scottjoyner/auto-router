# Benchmark Qualification Ladder

`src/auto_router/benchmark_qualification.py` records what a (node, model, task-family)
triple actually did at a rung of a fixed ladder, then proposes **one advisory role**.
It is a pure vocabulary plus pure functions over plain dicts.

## The boundary

This module is benchmark-only and cannot make anything routable.

- It imports nothing from `runtime_projection`, `runtime_projection_v2`, `admission`,
  `policy`, `main`, `main_live`, `claim_fence`, `executor_auth`,
  `install_benchmark_routing_policy`, or `install_enabled_discovery_policy`.
  A test walks the module's AST and fails the build if that ever changes.
- It defines no monkeypatch, no route, and never writes to `ModelConfig`
  (`task_family_scores`, `routing_roles`, `allow_code_execution`, `worker_mode`).
- It reads no live node state, endpoints, or provider config.
- Every output carries the guard keys `advisory_only: True`,
  `auto_load_allowed: False`, `mode: "simulation"`, `executable: False`,
  `mutates_model_config: False`, `creates_provider_eligibility: False`,
  `signed_admission_required: True`.

`build_qualification_report()` also emits a machine-checkable
`boundary_assertions` block asserting that provider eligibility, routing roles,
worker mode, and code-execution permission were **not** granted. Provider
eligibility remains the exclusive province of signed admission.

## The qualification ladder

Levels are a closed vocabulary and are nested: passing rung *N* implies every
lower rung.

| Level | What it proves |
| --- | --- |
| `L0_PROTOCOL` | deterministic tiny response; the wire protocol works |
| `L1_EXACT_GROUNDING` | answer one exact fact from the supplied context |
| `L2_SOURCE_GROUNDING` | name exact symbols and relationships from a bounded source slice |
| `L3_DIAGNOSIS` | identify root cause and minimal repair for a known bug slice |
| `L4_PATCH` | propose a patch, evaluated in an isolated temporary worktree |

Not every model reaches `L4_PATCH`, and reaching it is a description, not a
permission.

## The capability ladder

Seven axes, evaluated independently and **never collapsed**:

`endpoint_reachable`, `model_visible`, `model_loaded`, `protocol_usable`,
`grounding_usable`, `coding_usable`, `review_usable`.

A model can be listed but not loaded; loaded but protocol-broken;
protocol-clean but ungrounded; grounded but unable to produce a patch that
passes tests. `derive_capabilities()` reports each axis with the evidence basis
that decided it, and returns `collapsible: False`.

## Evidence schema

One row per (node, model, task-family) observation. Required fields:

`node_id`, `task_family`, `runtime_instance_id`, `runtime_kind`,
`runtime_version`, `model_instance_id`, `provider_model`, `alias`,
`quantization` (optional), `context_length`, `test_level`, `prompt_hash`,
`source_hash`, `succeeded`, `failure_mode`, `grounding_correct`, `patch_applied`,
`patch_valid`, `tests_passed`, `time_to_first_token_ms`, `tokens_per_second`,
`finish_reason`, `observed_at`, `confidence`.

`time_to_first_token_ms` and `tokens_per_second` follow the `route_events.py` /
`ledger.py` / `model_value.py` conventions. `observed_at` is an ISO-8601 string
like `quality_evidence.py`'s `last_observed_at`.

**No raw prompt text is ever stored.** Only `prompt_hash` and `source_hash`.
Auxiliary fields are counts and flags only (`usable_content_chars`,
`reasoning_chars`, `endpoint_reached`, `model_visible`, `model_loaded`,
`error_type`) — enough to classify a failure, never the content itself.

`normalize_evidence()` fills defaults defensively (`row.get("x") or 0`-style,
isinstance-guarded coercion) and records soft repairs in `rejections`. It raises
`ValueError` for structurally malformed rows: not a mapping, no `node_id`, no
model identity, an unknown `test_level`, or an unknown `failure_mode`.
`build_qualification_report()` counts such rows under `summary.rejected_rows`
instead of admitting them.

## Failure taxonomy

There is no generic `model_failed` bucket. Every non-passing observation is
attributed to exactly one named cause so the remediation is decidable:

`empty_usable_content`, `reasoning_only_output`, `length_truncated`, `timeout`,
`context_not_grounded`, `wrong_response_field`, `invalid_patch`, `test_failure`.

`classify_failure()` is deterministic: a declared `failure_mode` wins; then a
timeout-shaped `error_type`; then a length-shaped `finish_reason`; then empty
usable content split into *reasoning only* vs *genuinely empty*; then
patch/test/grounding/field checks. An unrecognised shape still resolves to a
named mode, never to "it failed".

## Role derivation

Exactly one of `CODE_QUALIFIED`, `REVIEW_QUALIFIED`, `SCOUT_QUALIFIED`,
`SUMMARY_ONLY`, `UNQUALIFIED`. Floors are checked strictest-first; the first
satisfied floor wins; `UNQUALIFIED` is the fallback.

| Role | min level | min pass rate | min grounding | min confidence |
| --- | --- | --- | --- | --- |
| `CODE_QUALIFIED` | `L4_PATCH` | 0.8 | 0.8 | 0.6 |
| `REVIEW_QUALIFIED` | `L3_DIAGNOSIS` | 0.7 | 0.6 | 0.5 |
| `SCOUT_QUALIFIED` | `L1_EXACT_GROUNDING` | 0.5 | 0.5 | 0.4 |
| `SUMMARY_ONLY` | `L1_EXACT_GROUNDING` | 0.0 | 0.0 | 0.2 |

Additional rules, all provable in `tests/test_benchmark_qualification.py`:

- A global confidence floor (default `0.5`) applies to every role. Evidence below
  it confers **no** role at all, not even `SUMMARY_ONLY`.
- Failure modes that invalidate a role's own deliverable block that role and no
  other: `invalid_patch`/`test_failure`/`timeout` block `CODE_QUALIFIED`;
  `empty_usable_content`/`timeout` block `REVIEW_QUALIFIED`; nothing blocks
  `SCOUT_QUALIFIED` or `SUMMARY_ONLY`. This is why L1 pass + L3 fail yields
  `SCOUT_QUALIFIED` rather than `UNQUALIFIED`.
- **Fast but wrong is `UNQUALIFIED` for coding.**
- **Slow but correct may still be `CODE_QUALIFIED`.**

## Why tokens/sec can never override a quality floor

`QUALITY_FLOORS` contains no throughput key. `evaluate_role_floor()` reads only
`highest_level_passed`, `pass_rate`, `grounding_accuracy`, `confidence`, and the
role's blocking failure modes. `tokens_per_second` and `time_to_first_token_ms`
are aggregated into `stats.advisory_throughput` and returned to the caller as
suggestions for *ordering* equal-quality candidates — they are never an input to
qualification. Each floor result also carries `throughput_considered: False`, and
the derivation carries `throughput_cannot_override_quality_floor: True`.

This is deliberate. A wrong answer delivered in 20ms is still wrong; throughput is
only meaningful between candidates that already cleared the same quality bar.
Speed is a tiebreaker, never a substitute.

## What the report gives you

`build_qualification_report(rows)` groups normalized evidence per
(node, model, task_family) and returns the guard keys, the ladder vocabulary, the
schema declaration (`stores_raw_prompt_text: False`), the boundary statement and
assertions, per-entry capability axes and role, and a summary with the role
histogram and the rejected-row count.

`next_benchmark_targets(report)` suggests the next rung to measure per model. It
emits `execution_mode: "dry_run"`, `requires_model_load: False`,
`requires_admission: True`. It suggests; it never schedules, loads, or admits.

## Integration boundary

The ticket allows this evidence to feed the benchmark planner, benchmark routing
policy, loadout reports, and operator dashboards, while forbidding it from making
an unadmitted node routable.

`apply_qualification_to_benchmark_plan(plan, report)` is that boundary in
practice. It **annotates** an existing plan and is the only sanctioned shape:

* no request is added, removed, reordered, or promoted;
* the only keys it adds are `benchmark_role` and `benchmark_role_confidence`;
* every other request field is byte-identical to the input;
* it never mutates the caller's document - it returns a new one;
* `advisory_only` and `auto_load_allowed` are forced to the safe values **even if
  the incoming plan claims otherwise**, so an authoritative-looking plan cannot
  launder its flags through this function;
* `creates_provider_eligibility` is pinned false and `signed_admission_required`
  true.

That last point is mutation-tested rather than asserted: deleting the
`advisory_only` assignment, and reprioritising an annotated request, each fail a
test.

The function does not import the planner. It accepts and returns plain dicts, so
the planner's own module graph is never pulled in and this module cannot become
a hidden dependency of scheduling.

For loadout reports and operator dashboards the report is consumed directly -
`entries[].role`, `entries[].role_floors`, and `summary.roles` are already
populated with the advisory guard keys attached.
