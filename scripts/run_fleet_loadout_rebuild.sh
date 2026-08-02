#!/usr/bin/env bash
set -euo pipefail

ROOT="${AUTO_ROUTER_ROOT:-/home/scott/git/auto-router}"
LOCK_FILE="${AUTO_ROUTER_FLEET_REBUILD_LOCK:-/tmp/auto-router-fleet-rebuild.lock}"
PYTHON="${PYTHON:-python3}"

: "${NEO4J_URI:?NEO4J_URI must be set}"
: "${NEO4J_PASSWORD:?NEO4J_PASSWORD must be set}"
export NEO4J_USER="${NEO4J_USER:-neo4j}"
export NEO4J_DATABASE="${NEO4J_DATABASE:-${NEO4J_DB:-neo4j}}"

cd "$ROOT"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "fleet rebuild already running; lock=$LOCK_FILE" >&2
  exit 75
fi

"$PYTHON" scripts/preflight_fleet_loadouts.py \
  --neo4j-uri "$NEO4J_URI" \
  --neo4j-user "$NEO4J_USER" \
  --neo4j-password "$NEO4J_PASSWORD" \
  --neo4j-database "$NEO4J_DATABASE"

"$PYTHON" scripts/build_fleet_loadouts_atomic.py \
  --neo4j-uri "$NEO4J_URI" \
  --neo4j-user "$NEO4J_USER" \
  --neo4j-password "$NEO4J_PASSWORD" \
  --neo4j-database "$NEO4J_DATABASE" \
  "$@"
