# x1-370 interrupted OS upgrade — operator-controlled recovery gate

**P0 HOLD / NO-GO.** Tracks Auto-Router issue #124 and separate NPU driver issue #122. This is not authorization to repair packages, reboot, drain production, install DKMS, modify Secure Boot, or restart services.

## Historical evidence — Nov 3, 2026 (not a new inspection)

Prior read-only physical evidence found Ubuntu 26.04.1 mid-upgrade: libc6 version 2.39 had half-installed/reinstallation-required status, locales were unpacked/unconfigured, approximately 23 packages were incomplete, and package dependencies conflicted with candidate glibc 2.43. GRUB, Shim and ROCm configuration also remained incomplete. The host continued serving Neo4j, AssistX, Docker and routing. Continued service is **not** proof of boot/rollback safety. The interruption cause is not established.

## Implemented source-only evidence collector

Run manually, only within a separately approved read-only evidence session:

    python3 scripts/x1_package_recovery_readonly.py --read-only

It emits only aggregate JSON to stdout, never a package inventory, secret, raw process environment, private host path, upgrade-log body or credential. It invokes only fixed read-only dpkg-query and optional mokutil status commands; reads os-release, meminfo, kernel and filesystem available capacity. It never invokes apt, dpkg configuration/repair, package download, Docker, systemd, SQL, Neo4j, swap changes, network requests, reboot or a write operation. The collector **always exits with code 2**, even for complete, clean-looking evidence, so a shell success status can never authorize maintenance. Structured JSON distinguishes observed states. **No report grants maintenance or release approval.** Missing console, root/EFI restore, rollback rehearsal and production admission remain HOLD.

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

A reported `NO_DPKG_INCOMPLETE_INSTALLED_DETECTED` state is never maintenance authorization; the collector always exits nonzero. The collector additionally requires installed/held `libc6` (not only `rc` residual configuration), parseable Ubuntu OS version, complete read-only memory fields, nonzero available root and EFI capacity, and independently parsed Secure Boot status. Missing EFI, unreadable `mokutil` evidence or missing memory fields yields `observations_complete=false`, even when dpkg counts are zero. All command outcomes exit 2; a complete JSON observation is informational only: no root+EFI restore, physical console, rollback rehearsal or maintenance approval is certified. All positive/negative tests use synthetic fixtures; no fleet host was changed.

## Read-only EFI mount attestation extension (November 4)

The collector now checks `/proc/self/mountinfo` (metadata-only, bounded to 1 MiB) for exactly one EFI mount record at the requested mountpoint, with a `vfat` filesystem and filesystem root `/`. A directory that simply exists or inherits free space from `/` cannot qualify as a mounted EFI volume. Missing, duplicated or non-VFAT mount records fail the preflight. Synthetic fixtures cover the negative cases, and no raw device paths or mount records are included in the report. This proves only the existence of the expected mount metadata: it does **not** prove the EFI backup is restorable, that a signed boot image will work, or that package repair is safe. Source-only research; production HOLD.

## Native glibc architecture proof (November 4 additional pass)

The x1-370 collector now explicitly requires an installed or held **native AMD64 libc6** package; an installed foreign `libc6:i386` cannot make the Ubuntu x86_64 host appear recovered. The aggregate records only a boolean `native_amd64_libc_installed`, not package source inventories. Fixture-only negative tests prove a foreign-only clean package state does not satisfy the native preflight and a half-installed native libc remains incomplete. This is specific to the documented AMD64 x1-370 host and is not a general ARM recovery collector. Live package repair or reboot remains prohibited by this draft.

## Verified host and EFI write-mode preflight (same-day iteration)

The read-only evidence collector now requires `uname().machine == x86_64`, in addition to native AMD64 libc6, to prevent interpreting an inventory from another host architecture as x1-370 recovery evidence. Its bounded EFI mountinfo check also requires a read-write (`rw`) VFat mount rather than merely any VFAT directory or read-only mount. Tests exercise a synthetic ARM64 host and read-only EFI mounts, both returning HOLD. These are observations about the host's current configuration, not authorization to remount EFI, repair glibc, rewrite firmware or reboot. The independently restorable root/EFI images, physical console and clone rehearsal are still mandatory.

## Core glibc version-consistency preflight — additional iteration

The aggregate read-only collector now denies a clean package-state verdict unless native AMD64 `libc6`, `libc-bin`, and `locales` all appear installed/held with exactly matching package versions. This catches a missing libc-bin/locales or a cross-release partially staged glibc cohort even if `dpkg-query` has no `iU`/`iHR` records. The check is conservative and **does not** prove package dependencies, GRUB/Shim, signed boot or a verified system restore. Synthetic fixtures cover missing, differing and removed glibc-core records. No package writes or repair were attempted; restoration and console gate remain P0 HOLD.

## Fail-closed shell status even on apparently clean package inventory (Nov 4 evening)

The `--read-only` package preflight deliberately exits **2 for every report**, including `observations_complete=true` with `package_state=NO_DPKG_INCOMPLETE_INSTALLED_DETECTED`. This eliminates the implicit-success path where an automation might treat exit `0` as authorization to run `dpkg`, `apt`, EFI updates, swap changes, or reboot. The JSON report retains detailed aggregate observations, with `decision=HOLD` and every production/repair/reboot permission false. A safe maintenance plan still requires independent, tested root+EFI restore images, out-of-band console and isolated-clone recovery rehearsal, beyond this collector's scope. Unit tests verify both clean-looking and incomplete reports exit nonzero.

## Off-host root and EFI image integrity evidence (November 4 late iteration)

A new independent `scripts/verify_x1_offline_recovery_images.py` helps the
designated restore operator assess **two already-created backup image files**
stored on a manually designated **isolated offline-media directory**.
The operator must independently supply pinned root-image and EFI-image
SHA-256 values from trusted custody records. Example on an *offline review
machine* (never a live disk or device node):

```bash
python3 scripts/verify_x1_offline_recovery_images.py \
  --isolated-offline-media-only \
  --media-root /mnt/offline-recovery-copy \
  --root-image /mnt/offline-recovery-copy/x1-root.img \
  --efi-image /mnt/offline-recovery-copy/x1-efi.img \
  --expected-root-sha256 "$EXTERNAL_ROOT_SHA256" \
  --expected-efi-sha256 "$EXTERNAL_EFI_SHA256"
```

The verifier only reads existing regular, non-hardlinked image files within
the selected directory, computes streaming SHA-256 digests with bounded
chunks, compares them with independently supplied values, refuses symlinks,
unexpected devices, bad paths and changing image metadata, and emits only
nonsecret aggregate JSON. **It deliberately always exits 2**, even when
both digests match. File hash agreement is NOT proof of externally witnessed
source identity, consistent filesystem snapshot, a successful restore,
usable EFI/GRUB/Shim boot path, complete Neo4j custody, or approval to run
live maintenance. Those independent gates remain HOLD. Fixture-only unit
tests verify tampering and unsafe path cases; no production backups have
yet been inspected by this new tool.

## Offline-media symlink race hardening (further iteration)

The separate image-hash reader no longer trusts a resolve-then-open sequence.
Every ancestor directory is opened using a held directory file descriptor
and `O_DIRECTORY|O_NOFOLLOW`; root/EFI image files are opened by descriptor-
relative `open` with `O_NOFOLLOW`. This rejects redirecting symlink parents,
including a symlink in the media-root ancestry or a nested backup directory,
and preserves the existing file type, size, change-during-read and digest
checks. The verifier remains off-host, explicitly operator-selected, read-only,
and **always HOLD**. Fixture tests include both symlink-parent negatives
and a valid nested image. No actual backup media were inspected. Source
checkpoint custody, bootability, clone restoration and maintenance approval
remain unsatisfied.

## Independently signed recovery-pair witness verification (new iteration)

Added `scripts/verify_x1_recovery_witness.py` to verify an **already-created**
root/EFI image pair against a compact externally signed Ed25519 manifest.
This closes a trust gap in the earlier comparison against two plain SHA-256
strings, which could otherwise be copied from the same untrusted host or
backup and falsely treated as independent custody.

The proposed version-1 witness manifest is strict canonical JSON (no
trailing newline; keys sorted; ASCII JSON; compact separators), containing
exactly `schema=x1.root-efi-custody-pair.v1`, `host_id=x1-370`,
`backup_set_id` (unique lowercase identifier), numeric `generation`,
`root_sha256`, `efi_sha256`, and `root_bytes`/`efi_bytes`. An
independent custodian signs **those exact canonical bytes** with an Ed25519
private key that **must never reside on the fleet host** or its backup.
The verifier accepts only a separately pinned public key, detached signature
and **minimum permitted generation from external custody**. It checks a
bounded no-follow manifest, duplicate/noncanonical keys, signature,
generation floor, and both image digests *and sizes*. Unknown or missing
fields, forged signatures, stale generations and mismatched images fail
closed. All output omits actual paths, digest values and signing keys.

On an isolated offline review machine, after separately obtaining
the trusted public key, signature and monotonic high-water generation from
the independent custodian, invoke:

```bash
python3 -m scripts.verify_x1_recovery_witness \
  --independently-witnessed-offline-only \
  --media-root /mnt/offline-recovery-copy \
  --manifest /mnt/offline-recovery-copy/x1-recovery-witness.json \
  --root-image /mnt/offline-recovery-copy/x1-root.img \
  --efi-image /mnt/offline-recovery-copy/x1-efi.img \
  --signature-hex "$EXTERNAL_WITNESS_SIGNATURE_HEX" \
  --trusted-public-key-hex "$EXTERNAL_WITNESS_PUBLIC_KEY_HEX" \
  --high-water-generation "$EXTERNAL_WITNESS_MIN_GENERATION"
```

This **cannot verify that the supplied public key or generation floor
really came from an independent source**; the report explicitly marks
`independent_key_custody_verified=false`. The owner must corroborate
these through an out-of-band trusted channel. The witness also cannot
prove the source snapshots were coherent, that an image is bootable,
the actual host's UUID/boot map, Neo4j consistency, or a successful clone
restore. Consequently, `gate=HOLD`, `production_repair_or_reboot_permitted=false`
and exit code **2** remain unconditional, even for valid signatures,
fresh generation and matching bytes. Synthetic ephemeral-key tests only;
no real backup or signing key was used.

## Off-host raw image structure triage (next iteration)

Added `scripts/inspect_x1_offline_boot_structure.py` as a second
**read-only, low-I/O** step to run on an isolated offline review machine
after verifying independently witnessed hashes/signatures. It opens each
selected regular file through descriptor-relative `O_NOFOLLOW` traversal,
reads at most 68 KiB per image and reports only known raw on-disk header
classes (ext, XFS, Btrfs, LUKS, GPT disk; FAT12/16/32 EFI partition).
It never invokes `mount`, QEMU, `fsck`, root commands, disk recovery,
package repair or host reboot.

```bash
python3 -m scripts.inspect_x1_offline_boot_structure \
  --explicit-off-host-read-only \
  --media-root /mnt/offline-recovery-copy \
  --root-image /mnt/offline-recovery-copy/x1-root.img \
  --efi-image /mnt/offline-recovery-copy/x1-efi.img
```

The classifications distinguish, for example, an ext partition image
from a GPT whole-disk image or a LUKS-encrypted image; they do **not**
prove filesystem integrity, EFI contents, bootloader paths, encrypted-key
availability, restorable partition alignment or that either file is the
actual root/EFI backup. Unknown headers are reported without asserting
corruption. It always returns exit code 2 and `decision=HOLD`. The
synthetic tests construct ext/FAT/GPT headers in temporary files and
exercise corrupt/symlink cases. A genuine restore to independent clone
storage and a witnessed firmware-to-userspace boot remain separate
mandatory steps.
