# Fleet loadouts and historical state

This document defines the canonical Neo4j shape for fleet loadouts, node/model snapshots, and change history.

## Goals

- Track what each node was running at a given time
- Track which model on which machine was assigned to which task class
- Preserve the raw fleet snapshot for later replay or analysis
- Record changes between snapshots so we can study drift, regressions, and improvements over time
- Support operational resiliency by making it easy to swap between known-good loadouts

## Core labels

### `FleetSnapshot`
Represents one fleet observation run.

Recommended properties:
- `id` — unique snapshot id, usually a timestamp + hash
- `captured_at` — datetime
- `source_job` — cron job or service name
- `source_host` — optional host or runner name
- `content_hash` — hash of the snapshot payload
- `raw_json` — full JSON snapshot payload
- `summary_json` — compact summary payload
- `change_json` — optional delta summary versus prior snapshot
- `notes` — optional freeform notes

### `FleetNodeState`
Represents one node’s state at a snapshot.

Recommended properties:
- `id` — stable key, e.g. `snapshot_id::node_name`
- `snapshot_id`
- `node_name`
- `node_ip`
- `online`
- `busy`
- `idle`
- `stale`
- `status`
- `latency_ms`
- `in_flight`
- `loaded_model_count`
- `model_count`
- `error`
- `payload_json`

### `FleetModelState`
Represents one model observed on one node.

Recommended properties:
- `id` — stable key, e.g. `snapshot_id::node_name::model_id`
- `snapshot_id`
- `node_name`
- `model_id`
- `display_name`
- `loaded`
- `context_length`
- `max_context_length`
- `speed_tps_ewma`
- `quality_ewma`
- `capabilities_json`
- `metadata_json`

### `FleetTaskProfile`
Represents a task class or workload family.

Examples:
- `coding_high_throughput`
- `coding_review_strict`
- `planning_strategy`
- `summary_extraction`
- `bulk_rewrite`
- `long_context_reasoning`

Recommended properties:
- `id`
- `name`
- `description`
- `quality_target`
- `latency_target`
- `throughput_target`
- `context_need`
- `notes`

### `FleetLoadout`
Represents a reusable fleet build for a task profile.

Recommended properties:
- `id` — stable slug, e.g. `coding_fast_deathstar_v1`
- `name`
- `task_profile_id`
- `version`
- `active`
- `quality_target`
- `latency_target`
- `throughput_target`
- `source_job`
- `captured_at`
- `notes`
- `summary_json`

### `FleetLoadoutAssignment`
Represents one slot in a loadout.

Recommended properties:
- `id` — stable key, e.g. `loadout_id::slot_1`
- `loadout_id`
- `slot_name`
- `node_name`
- `node_ip`
- `model_id`
- `role` — `worker`, `reviewer`, `fallback`, `backup`
- `rank`
- `enabled`
- `expected_tps`
- `quality_weight`
- `latency_weight`
- `reason`
- `notes`

### `FleetChangeDelta`
Represents the difference between two snapshots.

Recommended properties:
- `id`
- `from_snapshot_id`
- `to_snapshot_id`
- `captured_at`
- `change_json`
- `summary`
- `notes`

## Relationships

Recommended relationships:
- `(:FleetSnapshot)-[:HAS_NODE_STATE]->(:FleetNodeState)`
- `(:FleetSnapshot)-[:HAS_MODEL_STATE]->(:FleetModelState)`
- `(:FleetSnapshot)-[:HAS_LOADOUT]->(:FleetLoadout)`
- `(:FleetSnapshot)-[:HAS_CHANGE_DELTA]->(:FleetChangeDelta)`
- `(:FleetLoadout)-[:HAS_ASSIGNMENT]->(:FleetLoadoutAssignment)`
- `(:FleetLoadout)-[:OPTIMIZED_FOR]->(:FleetTaskProfile)`
- `(:FleetLoadoutAssignment)-[:TARGETS_NODE]->(:FleetNodeState)`
- `(:FleetLoadoutAssignment)-[:USES_MODEL]->(:FleetModelState)`
- `(:FleetChangeDelta)-[:FROM]->(:FleetSnapshot)`
- `(:FleetChangeDelta)-[:TO]->(:FleetSnapshot)`

## Suggested constraints

- Unique `id` on all Fleet* labels
- Index `FleetSnapshot.captured_at`
- Index `FleetLoadout.task_profile_id`
- Index `FleetLoadoutAssignment.loadout_id`
- Index `FleetNodeState.node_name`
- Index `FleetModelState.model_id`

## Loadout template

A loadout should be represented as a compact JSON object in `summary_json`, plus normalized nodes/relationships for queryability.

Example shape:

```json
{
  "id": "coding_fast_deathstar_v1",
  "task_profile": "coding_high_throughput",
  "objective": "maximize useful output per minute for coding and drafting tasks",
  "constraints": {
    "quality_floor": 0.72,
    "max_context_tokens": 32000,
    "prefer_loaded_models": true
  },
  "assignments": [
    {
      "slot_name": "primary_worker",
      "node_name": "deathstar-xps-8920",
      "model_id": "vibethinker",
      "role": "worker",
      "rank": 1,
      "expected_tps": 12.5,
      "reason": "fast throughput and low latency"
    },
    {
      "slot_name": "secondary_worker",
      "node_name": "scotts-macbook-air",
      "model_id": "vibethinker",
      "role": "worker",
      "rank": 2,
      "expected_tps": 9.1,
      "reason": "reliable overflow worker"
    },
    {
      "slot_name": "reviewer",
      "node_name": "x1-370",
      "model_id": "ornith",
      "role": "reviewer",
      "rank": 3,
      "expected_tps": 3.0,
      "reason": "slow but higher-quality review path"
    }
  ]
}
```

## Suggested fleet workflow

1. Probe live nodes and models.
2. Build a snapshot.
3. Score candidate loadouts for each task profile.
4. Pick the best loadout and persist it.
5. Link the loadout to the snapshot.
6. Record the delta versus the previous snapshot.
7. Use historical records to refine future loadout selection.

## Notes

- Keep the raw JSON payload forever if storage permits; it is the simplest way to make future analysis reproducible.
- Use stable ids so the same logical loadout can be compared across many snapshots.
- Prefer normalized relationships for graph queries, but keep JSON blobs for full-fidelity replay.
- For speed-sensitive work, `expected_tps` matters as much as model quality.
- For review-heavy work, quality and context length should outweigh raw throughput.
