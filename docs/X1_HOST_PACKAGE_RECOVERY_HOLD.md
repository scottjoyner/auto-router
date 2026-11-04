# x1-370 interrupted OS upgrade — operator-controlled recovery gate

**P0 HOLD / NO-GO.** Tracks Auto-Router issue #124 and separate NPU driver issue #122. This is not authorization to repair packages, reboot, drain production, install DKMS, modify Secure Boot, or restart services.

## Historical evidence — Nov 3, 2026 (not a new inspection)

Prior read-only physical evidence found Ubuntu 26.04.1 mid-upgrade: libc6 version 2.39 had half-installed/reinstallation-required status, locales were unpacked/unconfigured, approximately 23 packages were incomplete, and package dependencies conflicted with candidate glibc 2.43. GRUB, Shim and ROCm configuration also remained incomplete. The host continued serving Neo4j, AssistX, Docker and routing. Continued service is **not** proof of boot/rollback safety. The interruption cause is not established.

## Implemented source-only evidence collector

Run manually, only within a separately approved read-only evidence session:

    python3 scripts/x1_package_recovery_readonly.py --read-only

It emits only aggregate JSON to stdout, never a package inventory, secret, raw process environment, private host path, upgrade-log body or credential. It invokes only fixed read-only dpkg-query and optional mokutil status commands; reads os-release, meminfo, kernel and filesystem available capacity. It never invokes apt, dpkg configuration/repair, package download, Docker, systemd, SQL, Neo4j, swap changes, network requests, reboot or a write operation. Return code 2 means incomplete/unverifiable state, 0 means no incomplete installed dpkg states were detected. **Neither exit code grants maintenance or release approval.** Missing console, root/EFI restore, rollback rehearsal and production admission remain HOLD.

Fixture-only tests:

    python3 -m unittest discover -s tests -p 'test_x1_package_recovery_readonly.py' -v

No workflow or production timer invokes this collector automatically.

## Required owner-owned remediation gates

1. Establish independently checked and *actually restorable* root and EFI images, source/boot UUID mapping, trusted version-pinned package source, out-of-band physical console, restoration operator, approved interruption/rollback window. Disk-free space or a checksum alone is not a recovery proof.
2. Independently collect privileged **read-only** dpkg status, held-package/source evidence, upgrade logs (redacted), running and fallback signed kernel/boot entries, Secure Boot/MOK state, Neo4j graph backup custody, memory/swap reserve and other service identities. Keep raw records private.
3. Restore to an isolated clone and rehearse consistent glibc/package repair, locale/ROCm/GRUB/Shim configuration and boot under explicit rollback. Exercise negative/error paths and independent restore. Fail closed on unexpected prompts, package drift or boot issues.
4. Obtain separate explicit operator change authorization for live maintenance after service drain, business continuity and reversible recovery acceptance. Neither a green PR nor read-only preflight satisfies this.
5. After maintenance, independently prove current/fallback boot, dpkg consistency, Neo4j and service identity, protected Router admin endpoints, disk/storage custody, memory reserve and safe inference. Reopen NPU work only under its independent signed driver/firmware and model-runtime approval.

**Prohibited in this read-only scope:** apt --fix-broken, dpkg --configure, apt autoremove, swapoff, grub-install, kernel/DKMS installers, Docker/Neo4j restarts, model launches, firewall changes, credential changes, and reboot. Production remains HOLD.

## November 4 strict observation completeness extension

A reported `NO_DPKG_INCOMPLETE_INSTALLED_DETECTED` state is no longer sufficient for exit code 0. The collector additionally requires installed/held `libc6` (not only `rc` residual configuration), parseable Ubuntu OS version, complete read-only memory fields, nonzero available root and EFI capacity, and independently parsed Secure Boot status. Missing EFI, unreadable `mokutil` evidence or missing memory fields yields `observations_complete=false` and exit code 2, even when dpkg counts are zero. Successful collector exit is an informational preflight result only: no root+EFI restore, physical console, rollback rehearsal or maintenance approval is certified. All positive/negative tests use synthetic fixtures; no fleet host was changed.
