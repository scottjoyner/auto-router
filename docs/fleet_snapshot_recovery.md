# Fleet snapshot persistence recovery

The legacy `scripts/build_fleet_loadouts.py` writer reused mutable `FleetNodeState`
and `FleetModelState` nodes across snapshots and committed each query separately.
A failed or repeated run could therefore leave a partial snapshot and trigger a
Neo4j composite-constraint violation.

Use `scripts/build_fleet_loadouts_atomic.py` for subsequent rebuilds.

## 1. Inspect the failed snapshot

Run this read-only query first:

```cypher
MATCH (s:FleetSnapshot {
  snapshot_id: '18744e07-3ab2-46d5-97ba-cc00b5cd524e'
})
OPTIONAL MATCH (s)-[:HAS_NODE_STATE]->(n:FleetNodeState)
OPTIONAL MATCH (s)-[:HAS_MODEL_STATE]->(m:FleetModelState)
OPTIONAL MATCH (s)-[:HAS_LOADOUT]->(l:FleetLoadout)
RETURN
  count(DISTINCT n) AS node_states,
  count(DISTINCT m) AS model_states,
  count(DISTINCT l) AS loadouts;
```

Check whether any state node is also referenced by a different snapshot:

```cypher
MATCH (failed:FleetSnapshot {
  snapshot_id: '18744e07-3ab2-46d5-97ba-cc00b5cd524e'
})-[:HAS_NODE_STATE|HAS_MODEL_STATE]->(state)
MATCH (other:FleetSnapshot)-[:HAS_NODE_STATE|HAS_MODEL_STATE]->(state)
WHERE other <> failed
RETURN labels(state) AS labels,
       state.node_name AS node_name,
       state.model_id AS model_id,
       collect(DISTINCT other.snapshot_id) AS other_snapshots;
```

Do not delete shared legacy state nodes until their references are understood.

## 2. Guarded cleanup

Delete snapshot-owned loadout and assignment records first:

```cypher
MATCH (s:FleetSnapshot {
  snapshot_id: '18744e07-3ab2-46d5-97ba-cc00b5cd524e'
})
OPTIONAL MATCH (s)-[:HAS_LOADOUT]->(l:FleetLoadout)
OPTIONAL MATCH (l)-[:HAS_ASSIGNMENT]->(a:FleetLoadoutAssignment)
DETACH DELETE a, l;
```

Only delete node/model state that is not referenced by another snapshot:

```cypher
MATCH (s:FleetSnapshot {
  snapshot_id: '18744e07-3ab2-46d5-97ba-cc00b5cd524e'
})-[:HAS_NODE_STATE|HAS_MODEL_STATE]->(state)
WHERE NOT EXISTS {
  MATCH (other:FleetSnapshot)-[:HAS_NODE_STATE|HAS_MODEL_STATE]->(state)
  WHERE other <> s
}
DETACH DELETE state;
```

Then remove the failed snapshot and its delta:

```cypher
MATCH (s:FleetSnapshot {
  snapshot_id: '18744e07-3ab2-46d5-97ba-cc00b5cd524e'
})
OPTIONAL MATCH (s)-[:HAS_DELTA]->(d:FleetChangeDelta)
DETACH DELETE d, s;
```

## 3. Deploy and validate

```bash
cd /home/scott/git/auto-router
git fetch origin
git checkout agent/fix-fleet-snapshot-persistence
python3 -m pytest -q tests/test_build_fleet_loadouts_atomic.py
python3 scripts/build_fleet_loadouts_atomic.py --dry-run
python3 scripts/build_fleet_loadouts_atomic.py
```

The final JSON report must contain:

```json
{
  "build_status": "committed",
  "graph_committed": true
}
```

Validate the latest snapshot:

```cypher
MATCH (s:FleetSnapshot)
WITH s ORDER BY s.captured_at_ms DESC LIMIT 1
OPTIONAL MATCH (s)-[:HAS_NODE_STATE]->(n:FleetNodeState)
OPTIONAL MATCH (s)-[:HAS_MODEL_STATE]->(m:FleetModelState)
OPTIONAL MATCH (s)-[:HAS_LOADOUT]->(l:FleetLoadout)
RETURN s.snapshot_id AS snapshot_id,
       s.persistence_status AS persistence_status,
       count(DISTINCT n) AS node_states,
       count(DISTINCT m) AS model_states,
       count(DISTINCT l) AS loadouts;
```

Expected values for the reported fleet are approximately nine node states and
five loadouts. Model-state count depends on the models loaded at capture time.
