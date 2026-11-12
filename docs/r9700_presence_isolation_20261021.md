# Joyner R9700 presence heartbeat isolation (draft; not deployed)

## Physical evidence — October 21, 2026

Read-only GET captures from the existing x1-370 Auto-Router proved that `/api/fleet/node/joyner` alternates between two fresh payload shapes:

- **Fleet model reporter:** `library=[{"id":"joyner-toolcall","path":""}]`, `loaded=[]`, `power_profile=daily`, `power_model_class=full`, and CPU/system-RAM identity.
- **Unadmitted GPU presence heartbeat:** `library=[]`, `loaded=[]`, GPU-backend/device/VRAM keys, and no power-profile data.

The second format closely matches a **locally present, untracked** `scripts/fleet_r9700_heartbeat.py` in the router working directory. It posts unadmitted GPU presence and explicitly clears model library. A protected `joyner`-owned heartbeat process is also running every 60 seconds; its exact running code/hash has **not** been read and must not be assumed equivalent. The regular node reporter runs every 30 seconds. Both report under `hostname=joyner`, and the receiver previously overwrote `_node_reports["joyner"]` on every POST.

**This is a publisher-shaped collision, not verified loss of model processes.** Four `llama-server` workers remain running and Joyner's localhost model APIs are responsive.

## Candidate route-level remediation

In an **isolated source worktree only**, distinguish *one narrow, legacy, unadmitted Joyner R9700 GPU heartbeat schema* from ordinary model reports. To qualify as that diagnostic format, the request must have `hostname=joyner`, `library=[]`, `loaded=[]`, `capabilities=[]`, bool `health.gpu_canary_reachable_and_guarded`, `health.dispatch_admitted is False`, `health.toolcall_3b_unadmitted is True`, and GPU backend/device keys.

That heartbeat is handled as **unauthenticated advisory presence only**. It is recorded in a separate bounded, expiring, in-memory dict with only receipt time and explicit `UNVERIFIED_GPU_PRESENCE`. It is **not** written into `_node_reports`, **not** broadcast over the node report SSE/Redis stream, and cannot affect library, loaded models, latency/value scores or model dispatch eligibility.

Read-only `GET /api/fleet/presence/joyner` exposes bounded presence-status booleans, without raw model paths, GPU device values, tokens, credentials or health payload.

Ordinary node-report behavior is unchanged, including for model-bearing reports and non-Joyner hosts. Existing stored model reports continue to **expire normally** and are never refreshed by a GPU heartbeat. No evidence is merged across unauthenticated sources.

### Reproduction without production writes

```bash
cd /home/scott/git/wt-auto-router-r9700-presence-20261021
PYTHONPATH=src /home/scott/git/auto-router/.venv/bin/python -m pytest -q \
  tests/test_r9700_presence_isolation.py \
  tests/test_fleet_network_map.py \
  tests/test_fleet_live_discovery.py \
  tests/test_fleet_live_discovery_security.py \
  tests/test_model_value.py \
  tests/test_loadout_optimizer.py
```

The new tests use a disposable `FastAPI` `TestClient`. They do not transmit POSTs to the production router.

## Security limits / NO-GO

- **Shape classification is not identity or authentication.** The existing `/api/fleet/node-report` endpoint accepts unauthenticated self-reports, and a malicious regular model report can still spoof `hostname` and `loaded`. Fixing that requires a separate, authenticated, source-bound and lease/fencing-aware publisher contract (see [Auto-Router issue #37](https://github.com/scottjoyner/auto-router/issues/37)).
- The heartbeat's GPU device/card sysfs paths are not a trusted physical R9700 witness. The separate live PCI, VRAM, residency and timing qualification is still required.
- The change does **not** load IQ3_S, stop or alter any `llama-server`, change Auto-Router deployment, prove that a model is GPU-resident, approve a model, or choose the fastest/best model.
- Review this against other in-flight fleet-node-report and publisher-auth patches before merging; do **not** deploy as a shortcut around independent proof.

Related: [lms issue #50](https://github.com/scottjoyner/lms/issues/50) and [lms draft PR #49](https://github.com/scottjoyner/lms/pull/49).