# Benchmark-Aware Heterogeneous Fleet Routing

## Scope

`auto-router` does not discover or admit arbitrary Tailscale peers. It receives a
signed runtime projection from AssistX containing only fully approved, fresh,
identity-complete loaded runtimes. The projection may now include benchmark and
worker-role hints produced by the LMS fleet routing matrix.

The full Tailscale census remains visible through the AssistX context projection.
Observer-only devices are represented as blocked context nodes and never become
provider candidates.

## Input contract

An admitted provider may carry:

```json
{
  "routing_roles": ["auxiliary_llm", "summarization"],
  "worker_mode": "auxiliary",
  "allow_agent_runtime": false,
  "allow_code_execution": false,
  "models": [
    {
      "alias": "small-summary",
      "provider_model": "small-summary",
      "task_family_scores": {
        "summarization": {
          "quality_score": 0.75,
          "quality_confidence": 1.0,
          "reliability": 0.95,
          "tokens_per_second": 30.0,
          "utility_score": 0.82,
          "quality_floor": 0.52,
          "quality_floor_passed": true,
          "loadout_fingerprint": "..."
        }
      }
    }
  ]
}
```

These fields are signed with the rest of the AssistX runtime generation. They do
not create capacity and cannot make a model routable by themselves.

## Workload families

Routing recognizes:

- `coding`
- `reasoning`
- `tool_use`
- `long_context`
- `summarization`
- `compression`
- `extraction`

The family may be supplied in request metadata:

```json
{
  "model": "auto/compress",
  "metadata": {
    "task_family": "compression",
    "privacy": "local_only"
  }
}
```

Alias classification is available for auxiliary work:

| Alias | Profile |
|---|---|
| `auto/summarize`, `auto/summary` | `summarization_local` |
| `auto/compress`, `auto/compact` | `compression_local` |
| `auto/extract`, `auto/parse` | `extraction_local` |

Existing `auto/code`, high-quality, review, realtime, backlog, and local-only
profiles remain available.

## Candidate ordering

For a recognized family, the router:

1. starts with candidates from the normal strict-offline policy;
2. removes candidates whose signed worker roles do not permit the family;
3. ranks measured quality-floor passes first;
4. orders passed candidates by task-family utility and quality;
5. keeps unmeasured eligible candidates behind qualified evidence;
6. places measured quality-floor failures behind unmeasured candidates;
7. preserves the existing admission, load, LRU, latency, health, and private-path
   state as the final operational ordering signals.

An auxiliary worker may therefore win summarization or compression while being
ineligible for coding. High tokens per second never overrides a measured quality
failure.

## Roles

| Family | Permitted roles |
|---|---|
| coding | `full_agent`, `code_agent` with code execution allowed |
| reasoning | `full_agent`, `reasoning` |
| tool use | `full_agent`, `tool_agent` |
| long context | `full_agent`, `long_context` |
| summarization | `full_agent`, `auxiliary_llm`, `summarization` |
| compression | `full_agent`, `auxiliary_llm`, `compression` |
| extraction | `full_agent`, `auxiliary_llm`, `extraction` |

A projection without role or benchmark hints retains legacy routing behavior.
This supports staged deployment, but the production fleet should import and
validate the matrix before relying on heterogeneous role separation.

## Verification

```bash
pytest -q tests/test_benchmark_routing_policy.py
pytest -q
```

Inspect the active signed projection:

```bash
curl -fsS \
  -H "X-Admin-Token: $AUTO_ROUTER_ADMIN_TOKEN" \
  http://127.0.0.1:8088/admin/runtime-projection | jq
```

Inspect all discovered Tailscale nodes through AssistX context:

```bash
curl -fsS -u "$BASIC_AUTH_USER:$BASIC_AUTH_PASS" \
  http://127.0.0.1:8000/api/router/context-projection \
  | jq '.nodes | {count:length, nodes:map({node_id,lane,running,capabilities})}'
```

The context-node count should be greater than two on the intended fleet. This is
a visibility check, not a provider-count requirement. The provider count should
match only currently admitted, loaded runtimes.

## Safety properties

- Tailscale discovery cannot create provider capacity.
- Observer-only and benchmark-only peers never enter provider selection.
- Benchmark evidence is signed by AssistX before the router consumes it.
- Explicit physical runtime and loaded-model identity remain mandatory.
- Existing claim status is rechecked after queue wait and before dispatch.
- Public endpoints remain forbidden in strict-offline mode.
- The router never loads, unloads, starts, or stops a model or agent runtime.
