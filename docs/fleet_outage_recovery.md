# Fleet outage recovery

This runbook restores observation first. It does not automatically load or unload models.

## Root cause addressed

The previous `probe_all_nodes()` implementation read the last loadout report instead of
probing LM Studio. A bad report therefore became self-reinforcing. The strict-offline
deployment also disabled the fleet dispatcher and pinned the shadow reconciliation lane
to `refinedtoolcallv5-3b` on `x1-370`.

## 1. Stop mutating jobs

Temporarily stop the fleet assignment rebuild cron and any placement/autoload job. Keep
LM Studio servers running.

## 2. Update and inspect

```bash
cd /home/scott/git/auto-router
git fetch origin
git checkout agent/restore-live-fleet-discovery
python3 -m pip install -e '.[dev,legacy-loadout]'
pytest -q tests/test_fleet_live_discovery.py
python3 scripts/discover_fleet_models.py --timeout 5 --require-online 1 --require-loaded-models 1
```

Use `--json` to capture the complete inventory:

```bash
python3 scripts/discover_fleet_models.py --timeout 5 --json > data/fleet_discovery.json
```

## 3. Correct runtime URLs

An `OFFLINE` node with connection or DNS errors usually means its `LMSTUDIO_*_BASE_URL`
is wrong from the process or container network namespace. Test the exact URL from the
same shell/container that runs auto-router:

```bash
curl -fsS "$LMSTUDIO_X1_BASE_URL/models"
curl -fsS "${LMSTUDIO_X1_BASE_URL%/v1}/api/v1/models"
```

Repeat for each expected node. Do not assume `host.docker.internal` points to x1-370
when the router container is running on another machine.

## 4. Restore a known-good x1-370 model manually

First inspect what x1-370 actually has downloaded and loaded. On x1-370:

```bash
lms ls
lms ps --json
```

Prefer the known `ornith-1.0-35b` reasoning model on x1-370. Do not unload the only
working model until the preferred model has successfully loaded and passed a completion.
Use the exact model key shown by `lms ls`:

```bash
lms load <exact-ornith-35b-model-key> --identifier ornith-1.0-35b
curl -fsS http://127.0.0.1:1234/v1/models
```

Then run a minimal completion canary against the exact identifier.

## 5. Rebuild only after discovery is accurate

The discovery output should show every configured node, including disabled nodes, with
an explicit online/offline state. Only after the inventory is correct should the fleet
loadout rebuild be run in dry-run mode.

Do not restart automatic placement or autoload until routing selects the intended x1-370
model and at least one fallback node.
