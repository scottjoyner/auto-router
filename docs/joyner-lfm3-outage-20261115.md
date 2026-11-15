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

## Motherboard, BIOS and PCIe bridge acceptance — November 15, ~18:10 EST

The independent read-only DMI and PCIe root/bridge census identified:
- ASUS PRIME B650M-A AX II, BIOS 3014, DMI BIOS date 07/17/2024.
- 14 AMD PCI bridge functions, inventoried by current BDF with normalized
  current/max link speeds and widths only; the inventory was readable.
- Selected live bridge examples include current 0000:00:01.2 at
  32 GT/s x4, 0000:00:02.1 at 16 GT/s x4, 0000:00:08.1 at 16 GT/s x16,
  and 0000:03:00.0 at 16 GT/s x4. A bridge with a healthy local link
  does NOT prove that a formerly attached GPU endpoint is present.
- The R9700 1002:7551 PCI endpoint remains absent from complete PCI
  enumeration. The old 03:00.0 address is currently a different bridge:
  the historical BDF is *not* a stable device identifier.
- New read-only witness reports
  physical_r9700_slot_identified=false and bios_update_authorized=false
  even when all 14 bridge records are present.
- Latest live snapshot: LFM3 still has no HTTP health, restart count
  392; Kipnerter bridge restart count 807. Canonical MiniCPM and Ling
  remain independently service-healthy. Do not infer that 32 GT/s x4
  is the physical PCIEX16_1 GPU slot.

Official board specifications list one PCIe 4.0 x16 slot with x16
electrical connectivity (and three mechanically x16 slots operating
at x1); consult the actual board layout and expansion card power
instructions before identifying the R9700 physical slot:
https://www.asus.com/us/motherboards-components/motherboards/prime/prime-b650m-a-ax-ii/

ASUS firmware support lists BIOS v4004 published 2026-09-21, while this
board reports BIOS v3014. The ASUS page warns for version 3854 that
firmware cannot be rolled back to earlier versions. New BIOS
availability is a *review option only*, not evidence the installed BIOS
caused the GPU disappearance, nor authorization for a flash. A change
would require exact CPU/board/ROM compatibility review, vendor rollback
constraints, stable power and out-of-band recovery plan, full fleet
consumer and backup checks, and operator approval:
https://www.asus.com/us/motherboards-components/motherboards/prime/prime-b650m-a-ax-ii/helpdesk_bios?model2Name=PRIME-B650M-A-AX-II

The current SSH user cannot read /var/log/kern.log or /var/log/syslog;
journalctl -k and dmesg also fail. Treat kernel/PCIe cause as UNKNOWN
rather than "zero errors." The new source-only bridge witness does not
read either log channel, device PCI config bytes, SPI/BIOS flash, GPU
driver registers or any secret. Its synthetic tests cover accurate
board identity, untrusted link strings, malformed device BDF,
partial inventory, and denial of BIOS/slot admission on every outcome.

Release remains hardware-enumeration HOLD. No hotplug, unbind/rebind,
PCI bus rescan, slot/power toggle, BIOS update, GPU relocation, service
restart, lease/router cutover or access-control change was executed.

## Physical inspection and post-maintenance assessment — November 15

ASUS official E24013 user manual (June 2024), printed page 1-2
(PDF viewer zero-index page 11), identifies PCIEX16_1 as the TOP
full-length graphics slot directly below M.2_1. This one slot is
PCIe 4.0 x16 electrically. The three remaining full-length slots
are PCIe x1 electrically. This identifies the first slot to inspect
on a safely powered-down system, but current Linux sysfs cannot prove
which slot contains the missing R9700.

Official motherboard manual and layout:
https://dlcdnets.asus.com/pub/ASUS/mb/SocketAM5/PRIME_B650M-A_AX_II/E24013_PRIME_B650M-A_AX_II_UM_V2_WEB.pdf

The ASUS manual requires removing power before manipulating expansion
cards. The physical operator must first authorize a controlled
shutdown and power isolation, then document the GPU slot, latch,
auxiliary GPU power connectors, PSU cabling and any obvious damage.
Do not open a PSU or touch a card while powered. Obtain the specific
R9700 card vendor guide before assuming connector counts or wattage.
No hardware work is authorized by this research document.

The source-only post-maintenance assessor is
scripts/joyner_r9700_maintenance_assessment.py. It consumes JSON
from scripts/joyner_lfm3_outage_witness.py and makes NO live system,
network, file or service calls itself. For approved Fleet Commander
read-only execution:

    ssh -o BatchMode=yes -o StrictHostKeyChecking=yes joyner python3 - \
      < scripts/joyner_lfm3_outage_witness.py \
      | python3 scripts/joyner_r9700_maintenance_assessment.py

Both tools always terminate with HOLD exit 78.

A future candidate for human review must demonstrate a fresh same-boot
Joyner source receipt: correct ASUS board, PCI vendor/device 1002:7551
unique and stable, AMDGPU driver bound, discrete VRAM between 30000 and
36000 MiB, healthy port 19175, unchanged working canonical 1234/1236,
active required services and no restart growth during the observation.
Unknown/partial PCI, stale evidence, duplicated service records, wrong
host, changed boot or any source safety violation => HOLD with fixed
sanitized reasons. Healthy observations are only a CANDIDATE for review,
never permission for service or router cutover.

The source receipt is not cryptographically signed and is not sufficient
to establish physical power/slot inspection, resident GPU weights,
authenticated inference contract, ingress authentication, GPU lease,
operator signoff, or maintenance authorization. Even a fully positive
assessment prints verdict HOLD and exit 78. The assessor passed 27
synthetic adversarial offline tests, including malformed/untrusted JSON.

The live bounded read-only pipeline on Nov 15 returned HOLD with
R9700 absent, driver/VRAM unavailable and LFM3 :19175 unreachable.
The old service stop/disable, BIOS update, PCI rescan, GPU move,
reboot, rack power, key access and model restart remain unauthorized.

Kernel.dmesg_restrict is 1. Current SSH account cannot access kernel
journal or protected kern.log/syslog. Obtain an authorized, sanitized
early-boot PCIe/AMDGPU error receipt to diagnose why the GPU disappeared;
do not weaken kernel protections as a workaround.
