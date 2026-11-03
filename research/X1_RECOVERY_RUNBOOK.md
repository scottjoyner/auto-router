# x1-370 Offline Recovery — Ubuntu 26.04 interrupted release upgrade

Prepared 2026-11-03. Status: HOLD, NO LIVE PACKAGE OR BOOT MUTATIONS.
Tracker: https://github.com/scottjoyner/auto-router/issues/124
Dependent NPU driver: https://github.com/scottjoyner/auto-router/issues/122

## Confirmed state

- Ubuntu 26.04.1 resolute, running kernel 7.0.0-42-generic, original signed
  in-tree amdxdna active, Secure Boot enabled.
- November 2 upgrade stopped during package installation around the
  libc6 service-restart debconf question, cause of process termination
  not established.
- dpkg: libc6 2.39 half-installed/reinstall-required; libc-bin 2.39
  triggers pending; locales 2.43 unpacked; signed GRUB, Shim and ROCm
  components unpacked/unconfigured; 23 incomplete packages.
- apt-get simulated DKMS install fails. Simulated fix-broken plan proposes
  glibc, dpkg-dev and other upgrades plus GRUB/Shim/ROCm configuration.
  Simulation is NOT approval to run repair on the live system.
- Source root: /dev/nvme0n1p6, ext4, 958677385216 bytes (~893 GiB raw),
  about 609 GiB used. EFI: /dev/nvme0n1p1, vfat, 1073741824 bytes.
  Same NVMe contains Windows partitions; preserve their layout.
- NAS /nas/backups is CIFS with ~34 TiB free. 1 MiB exclusive-write/fsync/
  readback/SHA256 test passed. Rescue environment NAS access UNKNOWN.
- Fleet Commander x1 session runs with no-new-privileges and cannot
  sudo; this restriction must not be bypassed.
- Alternate kernel image/initrd files exist for 7.0.0-34/-38/-42,
  but successful alternate boot and physical rollback are not proven.
- Firmware boot records contain Ubuntu, Windows Boot Manager, and a
  generic removable device entry, not proof of an available rescue OS.

## Off-host evidence, NOT a bootable backup

- November 1 before-upgrade dpkg status snapshot:
  /var/backups/dpkg.status.1.gz
  SHA256 74be9040e76342f78758c86949ca75150e800b3ad55a93226f2fbd8c6ef007ff
- November 2 interrupted-upgrade dpkg status snapshot:
  /var/backups/dpkg.status.0
  SHA256 232da7591bda5821062ff5b2fe4ae649b177b382b5bb8dae795ddffd9e0795dd
- Four package-state records verified on Joyner:
  /home/scott/fleet-recovery-evidence/x1-370-20261103-h84MnXHr/x1-os-package-evidence.tar.gz
  SHA256 ec6b82d1e51c65d27bbedd54588adfabebae6bf08bd94d5ceadec8f19c774e24
- Six current EFI Ubuntu/fallback bootloader executables verified on NAS:
  /nas/backups/x1-370-efi-evidence-20261103-yv_8ehf0/efi-public-bootloader-evidence.tar.gz
  SHA256 9f703fe32dc8aeecd0ff16485cfd07899ce1163d1afb741f1acbcf6e388ee64a
- Never restore old dpkg status on top of mixed on-disk package files.
  The EFI archive is bootloader evidence, not a complete EFI image.

## Gate 0 — Recovery access (NOT MET)

1. Prove physical or out-of-band console, usable keyboard/display and
   an owner-tested rescue USB/ISO boot on this exact machine.
2. Arrange a service-drain and outage window with back-channel to a
   person who can recover if SSH/Tailscale go away. Preserve live
   inference, Neo4j, Docker, Caddy and routing.
3. Obtain NAS credentials via an approved secure method, never in
   shell command arguments, logs or GitHub issues.
4. Confirm hardware device identity and disk GPT by stable partition
   identifiers, twice independently. Do not touch Windows partitions.

## Gate 1 — Consistent, recoverable image (NOT MET)

1. Boot the trusted rescue OS with root and EFI source partitions
   UNMOUNTED, not in use by any operating system.
2. Re-identify root ext4 and EFI vfat by stable GPT PARTUUID,
   source device size and device identity. Halt on mismatch.
3. In the rescue environment, mount a scoped NAS backup destination
   and independently prove it is writable with durable round-trip
   readback. Reserve >=1.3 TiB headroom for raw-image equivalent,
   metadata, failure-atomic temporary files and operational margin.
4. Use an approved tool to image only source root and EFI partitions
   separately from unmounted source devices. Record GPT/UEFI metadata
   separately. Never overwrite another backup or unrelated partition.
5. Check full byte counts, cryptographic hashes, decompression and
   image readability. Ensure backup privacy and retention controls.
6. Independently restore and boot a clone, or prove a physical
   reversible restore path. A checksum alone is not restore proof.
7. Stop on mount, identity, network, checksum, capacity, permission
   or power irregularity.

## Gate 2 — Rehearse minimal OS package repair (NOT MET)

1. In a clone or isolated rollback-capable image, obtain privileged
   dpkg audit/logs, confirm root cause of libc debconf interruption
   and resolve package source/version consistency for resolute.
2. Review the exact APT transaction, removals, service restarts,
   signed GRUB/Shim and ROCm/driver compatibility; no autoremove or
   force flags as shortcuts.
3. Apply only the reviewed repair in the clone. Verify dpkg/apt clean
   state, secure boot, reboot, GPU/NPU identity, all services and
   recovery. Preserve a copy of the entire transaction log.

## Gate 3 — Owner-approved maintenance (NOT MET)

1. Require reviewed backups, verified rescue, rollback, service drain
   and explicit owner authorization.
2. Execute only the tested package plan during the outage. Validate
   packages and services, and perform a console-supervised boot.
3. Keep production NPU routing disabled until separate safety gates
   are met.

## Gate 4 — AMD XDNA 16K driver test (SEPARATE, NOT MET)

- AMD vendor source 2.25.260102.56 has a VM_PFNMAP -> VM_MIXEDMAP
  correction and compiles for the current 7.0.0-42 kernel.
- No vendor module has been signed, trusted, loaded or runtime tested.
- Require signed+trusted driver, independently rollback-capable boot,
  staging-only reproducible 16K NPU decode, and explicit approval.
- The production auto-router route remains NO-GO.

STOP if no verified console, consistent offline root+EFI image, recovery
drill, coherent package simulation, approved maintenance, or signed
module trust path. No in-place live package repair or module replacement.

## November 3 late read-only rescue-media and access follow-up

- The current 238 GiB removable VFAT drive contains DCIM and device
  management metadata, but no EFI/BOOT/BOOTX64.EFI and no observed
  rescue ISO. The existing firmware USB boot entry is NOT evidence of
  a verified rescue environment. Never overwrite that removable drive
  to create rescue media without separately establishing its ownership
  and preserving existing contents.
- ssh.service, rustdesk.service and tailscaled.service were active.
  These are operating-system-dependent remote access methods, not
  out-of-band console/recovery facilities.
- Fleet Commander NoNewPrivs=1, CapEff=0; sudo denied. Do not try to
  bypass containment, privilege boundaries or OS permissions.
- Safe research-only offline imaging readiness preflight:
  research/x1_offline_imaging_preflight.py --check
  It ALWAYS refuses to authorize imaging and identifies missing
  partition identity, source unmount, network filesystem, capacity,
  rescue privilege, console and restore gates. It handles both autofs
  placeholders and active CIFS mounts but does not mistake autofs alone
  for valid backup storage.
