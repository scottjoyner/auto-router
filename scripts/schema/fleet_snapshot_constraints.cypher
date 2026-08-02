// Run only after duplicate preflight queries return no rows.

CREATE CONSTRAINT fleet_snapshot_id_unique IF NOT EXISTS
FOR (s:FleetSnapshot)
REQUIRE s.snapshot_id IS UNIQUE;

CREATE CONSTRAINT fleet_node_state_snapshot_identity IF NOT EXISTS
FOR (n:FleetNodeState)
REQUIRE (n.snapshot_id, n.node_name) IS UNIQUE;

CREATE CONSTRAINT fleet_model_state_snapshot_identity IF NOT EXISTS
FOR (m:FleetModelState)
REQUIRE (m.snapshot_id, m.node_name, m.model_id) IS UNIQUE;

CREATE CONSTRAINT fleet_loadout_id_unique IF NOT EXISTS
FOR (l:FleetLoadout)
REQUIRE l.loadout_id IS UNIQUE;

CREATE CONSTRAINT fleet_loadout_assignment_id_unique IF NOT EXISTS
FOR (a:FleetLoadoutAssignment)
REQUIRE a.assignment_id IS UNIQUE;

CREATE CONSTRAINT fleet_change_delta_id_unique IF NOT EXISTS
FOR (d:FleetChangeDelta)
REQUIRE d.delta_id IS UNIQUE;
