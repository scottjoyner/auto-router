# x1-370 Ubuntu upgrade — read-only package repair baseline

Recorded November 3, 2026, approximately 18:39 EST. This is a SIMULATION
fingerprint, not an executable repair, approval, or boot recovery.

Issues: https://github.com/scottjoyner/auto-router/issues/124
Draft safety PR: https://github.com/scottjoyner/auto-router/pull/125

## Exact observed status and simulated transaction

Current dpkg status file SHA256:
232da7591bda5821062ff5b2fe4ae649b177b382b5bb8dae795ddffd9e0795dd

Read-only simulation command (does not acquire dpkg locks):
    apt-get -s -o Debug::NoLocking=1 -f install

- Eight package upgrades, one new, zero removals; 1725 not upgraded.
- The nine install-or-upgrade actions are: dpkg-dev, libc-bin,
  libc-dev-bin, libc-gconv-modules-extra, libc6, libc6-dbg, libc6-dev,
  libdpkg-perl, and make.
- The six glibc package versions target 2.43-2ubuntu2.4 for libc6,
  libc-bin, libc6-dbg, libc6-dev, libc-dev-bin and
  libc-gconv-modules-extra.
- Configuration actions include libc6, libc-bin, locales,
  grub-efi-amd64-signed, shim-signed, libamdhip64-7 and other packages.
- Canonicalized complete install/configure/remove plan SHA256:
  33475a9a474331a27e389c071142a95585a1b82dcc2467a3f39b714cc3263f21

Use research/x1_apt_repair_plan_audit.py --check to compare the current
package status SHA, complete plan digest, transaction counts, packages
and critical configuration steps. Every detected drift is held for
review. The script always reports HOLD and returns 2 in --check mode.
It never executes a package transaction.

## Reasons this is NOT sufficient for live repair

- APT simulation cannot prove maintainer scripts, trigger, EFI/GRUB
  writes, service restarts, recovery, or future boot will succeed.
- The Nov 2 release upgrader stopped at a libc6 service-restart dialog.
  Restarting fleet controller, Caddy, Docker, Tailscale and inference
  during a transaction risks losing remote access and running jobs.
- Signed GRUB/Shim and ROCm are among incomplete packages; secure
  bootloader, kernel, NPU, and GPU behavior need clone testing.
- No owner-tested physical rescue boot, consistent offline root+EFI
  image, or independently demonstrated restore exists.
- Fleet Commander process has NoNewPrivs=1 and is not privileged.
  Do not circumvent this restriction.
- Do not restore old dpkg status over mismatched binaries, run force
  flags, blindly apt autoremove, reboot or install a kernel module.

## Required sequence

1. Obtain an owner-controlled physical/OOB console, known bootable
   rescue medium, consistent unmounted root+EFI image to NAS, and
   independently tested recovery. Preserve existing Windows partitions.
2. On a clone, verify status hash and full simulated plan hash; any
   mismatch requires manual review of exact versions and actions.
3. Rehearse only the minimal repair in clone; review service restart
   behavior, bootloader signatures, Secure Boot and ROCm/NPU runtime.
4. During approved outage, drain services, execute only the rehearsed
   repair with a recoverable console, and check full fleet health.
5. Only after OS recovery consider separate signed AMD XDNA vendor
   16K driver validation in issue #122. Production NPU route remains
   NO-GO throughout.

This document contains no credentials or partition identity GUIDs.
