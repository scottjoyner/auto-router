# x1-370: Secure Boot-compatible rescue media — preparation only

**Status:** Research / owner-controlled physical boot **HOLD**. Do not modify
Secure Boot settings, firmware NVRAM, disks, existing removable media, or
production services. This does not establish that x1 can boot the image.

## Why SystemRescue 13.02 is not the default

x1-370 has Secure Boot enabled. The SystemRescue project explicitly states
its ordinary release does not support Secure Boot out of the box as of 2026.
Do **not** tell an operator to disable Secure Boot just to boot rescue media:
that could invalidate existing trust and complicate x1's signed AMD XDNA
rollback. Use a supported, signed Ubuntu live environment first.

Official references:
- SystemRescue forum:
  https://forums.system-rescue.org/t/cannot-boot-into-systemrescue-with-secure-boot-enabled/42
- Ubuntu Secure Boot documentation:
  https://documentation.ubuntu.com/security/security-features/platform-protections/secure-boot/
- Ubuntu ISO verification:
  https://documentation.ubuntu.com/security/software-integrity/image-verification/

## Candidate rescue image

Official Ubuntu 24.04.5.1 Desktop amd64 live ISO, which can be started in
"Try Ubuntu" mode *without installing* (but never assume this is a proven
x1 boot without testing). Vendor release path:
https://releases.ubuntu.com/24.04/ubuntu-24.04.5.1-desktop-amd64.iso

Official signed manifest:
https://releases.ubuntu.com/24.04/SHA256SUMS
Signature:
https://releases.ubuntu.com/24.04/SHA256SUMS.gpg

Publisher SHA256 for the ISO:
4da4a0c9035da8e68a59a838674f403f0a54472c78a83b4fb7f78d03588f85a7

Expected size observed in upstream HTTPS HEAD:
6,250,332,160 bytes (~5.82 GiB).

Publisher verification procedure: obtain SHA256SUMS and detached .gpg
signature, then verify with the trusted **Ubuntu CD Image Automatic
Signing Key (2012)**, full fingerprint:
843938DF228D22F7B3742BC0D94AA3F0EFE21092.

Use an **existing trusted Ubuntu keyring** rather than importing an
untrusted standalone public key without identity validation:
gpgv --keyring /usr/share/keyrings/ubuntu-archive-keyring.gpg SHA256SUMS.gpg SHA256SUMS

Only after successful signature verification may an operator compare a
complete ISO download to the verified SHA256. This says nothing about
device-level bootability.

## Fleet-specific recovery gates

1. Download and checksum on an **off-host** fleet node, not x1 (x1 has
   high load, nearly full swap, half-installed glibc).
2. Select a **new, empty and owner-approved** USB drive. Do NOT reuse
   x1's mounted 238 GiB VFAT camera/media drive. Writing a bootable
   image destroys destination contents.
3. Have trusted physical console/keyboard/monitor access and a
   separate rollback communications path before the first boot.
4. Validate image bytes on the USB independently after writing.
5. Confirm Secure Boot boots signed Ubuntu live media *without disabling
   Secure Boot*. Choose **Try Ubuntu**, never **Install Ubuntu**.
6. Boot a live environment with the source ext4 root and EFI partitions
   *unmounted*. Verify GPT PARTUUID, size, filesystem and source
   hardware. Ensure the NAS is writable from live media using a
   bounded test; do not put credentials in GitHub or on a public USB.
7. Only then create consistent separate root+EFI images to a scoped
   NAS destination, with hashes and independent restore verification.
   The root is ~893 GiB raw; preserve Windows partitions and GPT/NVRAM
   evidence. Do not apply any repair from live media before image
   integrity and rollback proof.
8. Rehearse the pinned APT package recovery in an isolated clone. Signed
   GRUB and Shim postinst scripts may alter EFI boot configuration.
   Owner approves explicit maintenance only after rollback works.

Related tracking:
- https://github.com/scottjoyner/auto-router/issues/124
- https://github.com/scottjoyner/auto-router/pull/125
- https://github.com/scottjoyner/auto-router/issues/122

This document is an operator review plan, not an automatic installer,
rescue-media writer, or authorization to reboot.
