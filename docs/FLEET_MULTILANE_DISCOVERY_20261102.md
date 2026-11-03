# Read-only multi-lane discovery validation (2026-11-02)

## Confirmed failure

`python scripts/discover_fleet_models.py --json --require-online 0 --require-loaded-models 0` aborted before probing with `duplicate LM Studio node_id 'xwing'`. The deployed registry legitimately contains multiple independent named provider lanes per physical host, including xwing and Destroyer. Both the legacy parser and the enabled-state monkey-patch independently rejected physical node duplicates.

## Source-only correction

Allow several named providers on one `node_id`, while retaining **unique provider names**, existing private-host endpoint validation, no URL-embedded credentials, and disabled-provider exclusion. CLI results remain one row per provider lane and keep the old `node_count` (lane-count) for backward compatibility, with explicit `physical_node_count`, `online_physical_nodes`, and `authoritative_physical_nodes` added. Source imports are pinned to the checkout rather than an unrelated editable package path.

**Graph write interlock:** Existing `FleetNodeState` is keyed by physical `node_name` rather than serving-lane ID. A multi-lane probe must be diagnostic-only for that writer: `build_fleet_loadouts.persist_to_neo4j` now rejects duplicate physical node rows **before any database access**, even with `--allow-empty-snapshot` or `--allow-degraded-snapshot`. A distinct, reviewed lane-aware model/endpoint reconciliation is required before persisting these observations.

`/v1/models` visibility is *catalog only*, not signed residency or permission to dispatch. A 401 on a protected model lane is not proof the model is offline. This code never edits the live provider registry, admits models, loads runtimes, or runs tasks.

## Live read-only validation

After patch: 11 enabled serving lanes on eight physical hosts. Five lanes returned some model endpoint data, two lanes had authoritative native LM Studio residency, three other reachable lanes were non-authoritative, and six lanes were unavailable or required credentials from the probe context. Three model residency identifiers appeared in the native records; this is not a token-generation count.

Production NO-GO remains until trusted publisher identity, fresh signed projection, authentic owner/claim/task admission, admin authentication, and native verified execution receipts pass independent release gates.
