# Joyner LFM3 and Kipnerter bridge outage — read-only evidence

**Observed:** November 15, 2026, approximately 17:28–17:32 EST.
**Tracking:** https://github.com/scottjoyner/auto-router/issues/178

## Confirmed live facts

Joyner restarted approximately 94 minutes before the initial 17:29 EST inspection. Its canonical fleet-joyner-gpu and fleet-joyner-ling services were active/running with zero systemd restarts after this boot, listening on TCP 1234 and 1236.

Two previously running services were unavailable:

- lfm3-joyner-inference.service: activating/auto-restart, Result=exit-code. NRestarts was 268 and rose to 290 during the investigation. No listener was observed on loopback TCP 19175; local GET /health returned no HTTP response.
- kipnerter-lfm3-r9700-bridge.service: activating/auto-restart, Result=exit-code. NRestarts rose from 552 to 596.
- The older joyner-lane-1234.service also remained in auto-restart, NRestarts rising from 1739 to 1876; canonical :1234 was already bound.
- LFM3 executable, model file (~0.98 GiB), and working directory exist and are readable. The bridge executable exists. This is not established to be a missing-path failure.
- Available system RAM was approximately 1576–1593 MiB; swap had about 3681–3682 MiB free. These are instantaneous observations, not proof of OOM or safe allocation. Root filesystem had approximately 69 GiB free.

**Root cause remains unverified.** journalctl --user returned exit 1 and no readable lines for all inspected units; a bounded systemctl status query did not yield a decisive error signature. Log unavailability is not evidence that no errors occurred. No reboot reason or agent attribution is established from the uptime.

## Reproducible bounded witness

scripts/joyner_lfm3_outage_witness.py is a two-sample, read-only observer, restricted to hostname Joyner and five explicit systemd unit names. It uses systemctl --user show with allowlisted property keys, ss -H -ltn for three exact ports, /proc boot+memory metadata and one local GET /health at 127.0.0.1:19175 per snapshot. It waits two seconds between snapshots to detect restart-count growth on the same boot.

The JSON contains only enum states, integer restart counts, boolean listening flags, health HTTP status, available RAM/swap and a SHA-256 of the boot identifier. It does not emit raw boot UUID, journal excerpts, ExecStart, unit file, Environment, model paths, keys, response bodies, raw sockets, process commands, GPU contents or prompts. It makes no inference request and never addresses a nonlocal service. It never sends credentials or runs a start/stop/restart/reload/disable.

The observer always exits 78 (HOLD). On missing sockets, failed systemd reads, changed boot identity or unknown values it does not invent a PASS. Healthy status would still require independent rollout approvals.

Run as an already-approved Fleet Commander read-only diagnostic, using the existing SSH transport, feeding the script through stdin without installing it on Joyner:

    ssh -o BatchMode=yes -o StrictHostKeyChecking=yes joyner python3 - \
      < scripts/joyner_lfm3_outage_witness.py

Expect one JSON object and status 78. Do not pipe this into an auto-remediator. This is not a background monitor.

The dedicated .github/workflows/joyner-lfm3-outage-witness.yml runs only synthetic unit tests on exact PR HEAD, with contents: read permission. Hosted CI never contacts Joyner or any protected service.

## Safe next recovery gates

1. P1 HOLD: LFM3 health must be shown available through a known source/boot/runtime snapshot before considering service promotion.
2. Independent root cause receipt: Obtain bounded, sanitized failure evidence from an existing authorized logging/owner channel. Distinguish GPU/model-load failure, memory pressure, unit command failure, bridge upstream failure or port ownership. No unfiltered secrets or argv.
3. Resource/consumer authorization: Confirm which production routes rely on LFM3 vs canonical MiniCPM/Ling; preserve working consumers and authentication boundaries.
4. Separately approved change: Record the narrowest service-specific fix, exact source revision, rollback, health and negative tests, GPU headroom/lease and operator authorization.
5. Post-change evidence (only if approved): Stable port 19175 and authorized bridge health, no growing restart counters, preserved MiniCPM/Ling routes, no authentication regression, exact-head CI, and signed operator/custody receipt.

Do not stop/restart models, change live systemd files or GPU layers, relax permissions, inspect credentials, alter ingress/router, launch training, or merge diagnostics as a substitute for release gates. Legacy duplicate :1234 issue #176 and ingress-auth issue #81 are separate.

## Material dGPU enumeration regression — November 15, 2026

The independent November 8 source-of-truth in Auto-Router issue #150
documented Radeon AI PRO R9700 at PCI 0000:03:00.0 (AMD 1002:7551),
AMDGPU bound, about 32 GiB discrete VRAM, and an existing LFM3 runtime
with a DRM GPU client. The November 15 17:32 EST read-only PCI inventory
instead finds only the AMD Raphael integrated VGA device 1002:164e
and associated audio 1002:1640. The sole graphics card has 512 MiB VRAM,
and only /dev/dri/card0 and renderD128 are present.

The outage witness now inventories all available PCI devices for exact
AMD vendor/device 1002:7551 rather than assuming a permanent PCI bus
address. It independently records whether PCI sysfs enumeration was
readable, a unique R9700 identity was present, its amdgpu driver binding,
its total discrete VRAM when available, and an allowlisted PCI BDF.

Live result at approximately 17:34–17:36 EST: PCI inventory usable,
r9700_pci_present=false, driver/VRAM evidence unavailable, no exact
R9700 location; repeat snapshot identity stable. LFM3 :19175 still down,
LFM3 auto-restart 317, bridge 650; canonical MiniCPM/Ling remain active.
The tool marks r9700_runtime_capability_verified=false and
lfm3_failure_attributed_to_r9700_loss=false. In other words, the absent
dGPU is a verified major regression and plausible prerequisite failure,
but a direct causation chain is NOT proven from the unreadable journal.

**Do not blindly restart model services, reload amdgpu, perform a PCI
rescan, flash BIOS, reboot, pull/reseat the card, cycle rack/Govee power,
change GPU assignments, or move the GPU to xwing.** Those are separate
state-changing maintenance actions requiring operator authorization,
hardware inspection, a power/slot/firmware/driver diagnostic, known-good
rollback and verified host-service consumers. First obtain a bounded
boot-time kernel/PCIe error classification from an authorized logging
channel. Fail closed if no accessible evidence source exists.

The source-only PCI test uses fake sysfs directories and synthetic IDs;
hosted CI never touches a physical PCI bus.
