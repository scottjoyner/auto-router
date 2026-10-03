# Benchmark Model Qualification

_Added: 2026-10-02_

## Why

The fleet campaign reported:

```text
OptiPlex   degraded
Lenovo     degraded
Destroyer  unavailable
Xwing      high-load / excluded
```

Several of those endpoints were reachable. Endpoint availability did not
correlate with useful grounded coding performance, so every node was correctly
unqualified — but the system could not say *why*, and could not distinguish a
model that is useless from a model that was never properly measured.

Those are four different claims, and this pipeline keeps them separate:

```text
endpoint reachable
model visible
model loaded
protocol usable
grounding usable
coding useful
review useful
```

`src/auto_router/benchmark_qualification.py`.

## Qualification ladder

| Level | What it establishes |
|---|---|
| `L0_PROTOCOL` | Deterministic tiny response. Transport works. **Earns no lane.** |
| `L1_EXACT_GROUNDING` | Answers one exact fact from supplied context |
| `L2_SOURCE_GROUNDING` | Identifies exact symbols/relationships from bounded source |
| `L3_DIAGNOSIS` | Identifies root cause / minimal repair for a known bug slice |
| `L4_PATCH` | Proposes a patch, evaluated in an isolated temporary worktree |

Not every model needs to reach L4. Most of this fleet should not. The ladder
exists to measure honestly, not to sort a leaderboard.

`L0` earns no role on purpose: a deterministic tiny response proves the
endpoint is alive, not that the model is useful. Reporting `L0` as a lane is how
a broken node looks healthy again.

## Role derivation

The highest level that actually **passed** determines the role:

| Highest passed | Role |
|---|---|
| `L4_PATCH` | `CODE_QUALIFIED` |
| `L3_DIAGNOSIS` | `REVIEW_QUALIFIED` |
| `L2_SOURCE_GROUNDING` | `SCOUT_QUALIFIED` |
| `L1_EXACT_GROUNDING` | `SUMMARY_ONLY` |
| none / L0 only | `UNQUALIFIED` |

Two additional conditions, both fail-closed:

- **Quality floor** (`QUALITY_FLOOR = 0.5`). A level below the floor is failed.
- **Confidence floor** (`ROLE_CONFIDENCE_FLOOR = 0.5`). Evidence too thin to be
  confident about yields `UNQUALIFIED` rather than a guess. We would rather
  report nothing than advise a lane off one sample.

### Throughput never buys quality

`tokens_per_second` is **not an input to role derivation**. It is recorded, and
it is surfaced in the report so an operator can see it, but a 5000 tok/s model
that failed grounding stays `UNQUALIFIED`. This is the single most important
property of this module: the previous failure mode was a fast, confident,
wrong model looking like a good one.

Throughput may break ties *within* an already-earned role. It cannot create one.

## Evidence schema

`QualificationEvidence` — one ladder attempt, `extra="forbid"`:

```text
node_id, runtime_instance_id, runtime_kind
model_id, quantization, context_tokens
task_family, test_level
prompt_sha256, source_sha256            # digests only
outcome, failure
quality_score, grounding_correct, patch_sha256, test_result
ttft_ms, tokens_per_second, finish_reason
observed_at, confidence
```

**No raw prompt persistence.** This is structural, not a convention: prompts and
sources are recorded only as SHA-256 digests, so there is no field in which
sensitive prompt text could be stored. `model_dump()` cannot produce one.

`qualification_id` follows the existing colon-joined convention
(`benchmark_planner.py`): `node:model:family:level`.

## Failure taxonomy

Collapsing these into a generic `model_failed` destroys the operator's ability to
tell a broken endpoint from a broken model from a broken prompt — which need
completely different responses.

| Failure | Meaning |
|---|---|
| `EMPTY_USABLE_CONTENT` | Nothing usable in the expected field |
| `REASONING_ONLY_OUTPUT` | Content empty, reasoning field populated |
| `LENGTH_TRUNCATED` | Generation hit the length cap mid-answer |
| `TIMEOUT` | Did not complete inside the deadline |
| `CONTEXT_NOT_GROUNDED` | Answer not derivable from supplied context |
| `WRONG_RESPONSE_FIELD` | Answered, but not in the protocol's field |
| `INVALID_PATCH` | Syntactically present, did not apply |
| `TEST_FAILURE` | Patch applied, tests rejected it |
| `PROTOCOL_UNUSABLE` | Transport fine, protocol broken |
| `NOT_ATTEMPTED` | No evidence collected |

`EMPTY_USABLE_CONTENT` and `REASONING_ONLY_OUTPUT` are kept separate
deliberately. In the second case the endpoint is healthy, the model is loaded,
and the transport worked — the model reasoned and never emitted an answer.
Folding it into "empty" would send an operator to the wrong subsystem.

`TRANSPORT_FAILURES` groups the harness-level causes so a flaky node is not
misfiled as a weak model.

## Integration boundary

`build_qualification_report()` output may feed:

```text
benchmark planner
benchmark routing policy
loadout reports
operator dashboards
```

Every report carries an explicit authority block:

```json
{
  "advisory_only": true,
  "auto_load_allowed": false,
  "authority": {
    "advisory_only": true,
    "creates_provider_eligibility": false,
    "changes_production_admission": false,
    "changes_routing_authority": false,
    "changes_signed_runtime_projection": false
  }
}
```

Benchmark evidence **cannot** make an unadmitted node routable. The production
admission path (`admission.py`), the signed runtime projection
(`runtime_projection.py`, `runtime_projection_v2.py`) and provider eligibility
(`policy.py::PolicyEngine._provider_is_eligible`) are untouched. No production
routing, admission or projection module was modified.

`apply_qualification_to_benchmark_plan()` annotates an existing plan in place
without adding, removing, reordering or promoting requests, and preserves the
plan's own `advisory_only` / `auto_load_allowed` flags. Pairs with no evidence are
left untouched rather than defaulted into a lane.

## Tests

`tests/test_benchmark_qualification.py` — 26 deterministic tests covering:

```text
fast but wrong            -> UNQUALIFIED for coding
slow but correct          -> may qualify
L1 pass / L3 fail         -> scout/extraction role only
L0 only                   -> UNQUALIFIED (transport proven, usefulness not)
timeout                   -> explicit TIMEOUT, not a generic failure
empty content + reasoning -> REASONING_ONLY_OUTPUT, distinct from empty
quality floor failure     -> cannot be won from high TPS
low-confidence evidence   -> no role
all-timeouts              -> no qualified level
benchmark evidence       -> cannot create provider eligibility
plan annotation           -> preserves advisory flags and structure
```

## Scope

Benchmark-only qualification evidence. Not a routing change, not an admission
change, not a projection change.