# x1-370: static package-script impact review — research only

Prepared November 3, 2026. This is NOT a package-repair approval.
No maintainer script, dpkg transaction or package setup command was run.

## Exact pre-install prompt identified

The interrupted November 2 distribution-upgrader log ends in the debconf
question: Restart services during package upgrades without asking?

The read-only local debconf templates database has this exact Description
under libraries/restart-without-asking, with Default: false
(/var/cache/debconf/templates.dat near line 7972).

The same template occurs in the templates member of the cached
Ubuntu 26.04 libc6_2.43-2ubuntu2.4_amd64.deb. The cached package SHA256
matches its APT index:
a613b457f3ff9c84ebd28af8a05afdec22605ad9aed87c71e3a6ae5170aeb9ca

The libc6 static preinst also enumerates installed NSS-using services
and queries debconf along its glibc/upgrade path. The exact question
is independently grounded in the template registry, not guessed from
the generic glibc/upgrade question. The terminal log is strong evidence
of an interrupted interactive phase, not proof of termination mechanism.

## GRUB/EFI and Secure Boot risk

Installed unpacked grub-efi-amd64-signed.postinst has a configure branch
that conditionally calls grub-multi-install or grub-install if
/boot/grub/x86_64-efi/core.efi exists. THIS FILE EXISTS ON X1.

Installed unpacked shim-signed.postinst also conditionally calls
grub-multi-install and has optional DKMS and MOK signing/enrollment
migration. Currently dkms command and MOK.priv/MOK.der were not found
on x1 in read-only inventory. Exact script branch execution is
unverified until clone rehearsal.

Observed source SHA256:
- grub-efi-amd64-signed.postinst:
  97bd82783ba656587981d0f579c229281ed8ed4bec433f2861f1696d846c673e
- shim-signed.postinst:
  6664a087f69e682d46bd8a95e55c12d0e8e32f52020c26f9d7da876d5325a24b

Repair MAY modify EFI System Partition and firmware boot configuration.
Simulation success is not bootloader safety proof. x1 has Secure Boot
enabled and Windows NTFS partitions alongside Linux. Preserve these.

## Exact cached repair bundle

Nine candidate package files matched cached Ubuntu APT index SHA256
and dpkg-deb package/version/architecture. Copied with fsync to:
/nas/backups/x1-370-offline-apt-debs-20261103-cskmisux

9 files, 14,438,010 bytes; manifest SHA256:
9b899a5c15a20df66bf9ae97d5c098aa19d3da3eca76fba20c632752eaf77cbc

Joyner, Deathstar and xwing each independently read back all nine
package sizes and hashes: 3/3 PASS. Read-only verifier:
research/x1_offline_pkg_bundle_audit.py

This does not constitute a bootable recovery or guarantee package
maintainer scripts will execute safely.

## Required gates

1. Owner-tested physical/out-of-band console and rescue boot; confirm
   devices by GPT IDs; make consistent offline UNMOUNTED root/EFI image
   to NAS; verify independent restore and boot. Do not repurpose
   existing camera removable media without approval/backup.
2. In isolated clone, rehearse libc6 debconf service choices and service
   restart list, GRUB/EFI install branches, Shim Secure Boot/MOK paths,
   and ROCm compatibility. Inspect what might restart or disconnect.
3. Verify exact target plan digest:
   33475a9a474331a27e389c071142a95585a1b82dcc2467a3f39b714cc3263f21
   and current dpkg status digest:
   232da7591bda5821062ff5b2fe4ae649b177b382b5bb8dae795ddffd9e0795dd
   Any drift requires HOLD and new review.
4. Owner-approved service-drained outage and independently tested
   recovery are required for any real package transaction. No forced
   noninteractive shortcuts or unreviewed autoremove/grub-install.
5. NPU driver and 16K routing remain independent downstream NO-GO.

Issue: https://github.com/scottjoyner/auto-router/issues/124
Draft PR: https://github.com/scottjoyner/auto-router/pull/125
