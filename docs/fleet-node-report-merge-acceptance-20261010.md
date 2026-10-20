# Fleet node-report producer merge: acceptance and deployment boundary

**2026-10-10**, source branch `fix/fleet-node-report-merge-20261010`.
This is a code/test change only, **not an authorization to restart production**.

## Live evidence motivating the patch

Deathstar runs two user accounts, each with a fleet-node-reporter service,
and an additional AssistX fleet-node-agent task loop. At inspection, both
reporter instances attempted publication to `/api/fleet/node-report`.
The old `deathstar`-account reporter advertises zero models from dead port
1234; the separate `scott` reporter is richer but also defaults to 1234.
The batch agent sends `node_id` without `hostname`. The current router
puts those sparse messages under a shared `unknown` hostname and drops
the previously observed model information when other publishers replace
a node row wholesale.

The **redundant deathstar-account reporter was disabled** after verifying
the scott reporter and task agent were still active. Neo4j, dashcam
monitor and rsync remained running. No model loading or task dispatch change.

## Code contract

- Identity priority: `hostname` -> `host_name` -> `node_id` -> `unknown`.
- Merge only fields explicitly present in the latest publisher payload.
- An explicit `loaded: []` clears model residency; a missing `loaded`
  field means the agent made no loaded-state claim.
- Each field has an independent `field_received_at` clock. Sparse heartbeats
  **must not** extend the loaded-model evidence freshness window.
- Fields not refreshed within 180 seconds expire to empty / unknown.
- Dead node identities are removed from in-memory storage after 24 hours.
- SSE and Redis fan-out receive the **merged** report.
- New `health.model_endpoints` evidence can be accepted later without
  granting model dispatch (keep `loaded` separate until acceptance).

## Acceptance gates

1. Targeted tests: `test_fleet_node_report_merge.py`.
2. Regression: `test_fleet_network_map.py`, `test_runtime_projection.py`.
3. New stale test: agent heartbeat >180 s after reporter evidence must
   retain fresh capabilities while expiring loaded and library claims.
4. Production rebuild must be scheduled separately to protect active
   gateway sessions. Validate no `unknown` phantom after node refresh.
5. Do not promote Deathstar to default inference until human end-to-end
   client/gateway/trace acceptance with bounded GPU/CPU and storage leases.

## Storage constraints

Beelink `/nas` remains above acceptable write pressure. New bulk
traces and backups require L1/L5 admission; historical source release
remains blocked. No production router change can override storage gates.