# Joyner R9700 PCI startup condition and bounded retry policy — research

Observed 2026-11-16 17:29–17:35 EST via read-only strict-host SSH:
R9700 PCI ID 1002:7551 absent, ~524 MiB RAM available, inference service
4,362+ restarts, bridge 9,013+ restarts. The inference/bridge restart
intervals are 20s/10s, but both StartLimitIntervalUSec values are 10s.
This does not meaningfully contain consecutive failures 10–20s apart.

## Proposal, not installed
r9700_pci_condition.py checks max 512 immediate sysfs PCI device records,
strict AMD vendor 0x1002 and device 0x7551, requiring exactly one match.
Absent, duplicated, malformed or unreadable => exit 1 (systemd skip);
one enumerated device => exit 0 (PCI presence only). No model/GPU driver/
network/credential/NAS/graph actions. Driver, ROCm, VRAM, model, API,
training readiness remain independently unverified.

The two proposed service drop-ins constrain starts to 3 per 600 seconds
and use an ExecCondition referencing a FUTURE verified helper installation
at /opt/fleet/bin/r9700_pci_condition.py. They are not installed.
Missing device means the service is skipped, so restoration of hardware
needs a separately authorized new start; there is no automatic resume.

## Operator release and rollback gates
1. Physically verify GPU PCIe seat, slot, cabling, power, BIOS and hardware
   identity before touching any runtime or driver.
2. Record current unit source, dependencies and owner, and establish
   maintenance window and approved rollback. Verify helper digest and path.
3. Review ExecCondition semantics and service-specific canary plan.
   Do not expose protected unit environment or secrets in GitHub.
4. Owner approves a bounded staging trial only after PCI enumerates;
   measure failure behavior, driver/ROCm and real runtime readiness
   separately. Retain existing model/credential/agent release gates.
5. If helper path is missing, ExecCondition itself can fail to start;
   verification and deployment ordering are therefore mandatory.
6. No live unit edit, reload, stop/start/restart, PCI rescan, model call,
   driver install, mount, agent lease or credential change performed.

Offline tests: python3 -m pytest -q test_r9700_pci_condition.py
