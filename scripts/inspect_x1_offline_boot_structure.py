#!/usr/bin/env python3
"""Read-only, off-host structural triage of independently held x1 backup images.

Recognizes only a few standard raw partition/disk headers. A positive
signature is NOT a verified backup, mount, guest boot, or valid restore.
Never opens block devices, invokes mounts, modifies files, or exits success.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import zlib

from scripts.verify_x1_offline_recovery_images import (
    MAX_IMAGE_BYTES,
    _open_directory_nofollow,
    _under,
)

PREFIX_BYTES = 68 * 1024


def _prefix(media_root: Path, image: Path) -> bytes:
    if not _under(media_root, image):
        raise ValueError("image outside isolated offline media")
    parent = _open_directory_nofollow(media_root)
    components = image.relative_to(media_root).parts
    try:
        for part in components[:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
            os.close(parent)
            parent = child
        fd = os.open(
            components[-1],
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
    finally:
        os.close(parent)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= MAX_IMAGE_BYTES):
            raise ValueError("image must be a single regular file")
        data = os.pread(fd, PREFIX_BYTES, 0)
        after = os.fstat(fd)
        if (before.st_dev != after.st_dev or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns):
            raise ValueError("image mutated during bounded structure read")
        return data
    finally:
        os.close(fd)


def _valid_primary_gpt_header(data: bytes) -> bool:
    """Validate bounded primary GPT header, not the partition array or backup.

    This is header-integrity triage only, not a complete disk validation.
    Never infer a bootable disk from the result.
    """
    sector = data[512:1024]
    if (len(sector) != 512 or sector[:8] != b"EFI PART"
            or data[510:512] != b"\\x55\\xaa"
            or data[450] != 0xEE):
        return False
    header_size = int.from_bytes(sector[12:16], "little")
    if not 92 <= header_size <= 512:
        return False
    header = bytearray(sector[:header_size])
    expected_crc = int.from_bytes(header[16:20], "little")
    header[16:20] = b"\\0" * 4
    if zlib.crc32(header) & 0xFFFFFFFF != expected_crc:
        return False
    current_lba = int.from_bytes(sector[24:32], "little")
    backup_lba = int.from_bytes(sector[32:40], "little")
    first_lba = int.from_bytes(sector[40:48], "little")
    last_lba = int.from_bytes(sector[48:56], "little")
    partition_lba = int.from_bytes(sector[72:80], "little")
    partition_count = int.from_bytes(sector[80:84], "little")
    partition_size = int.from_bytes(sector[84:88], "little")
    return (
        current_lba == 1
        and backup_lba > 2
        and 2 <= partition_lba < first_lba <= last_lba < backup_lba
        and sector[20:24] == b"\\0" * 4
        and sector[56:72] != b"\\0" * 16
        and 1 <= partition_count <= 16384
        and 128 <= partition_size <= 1024
        and partition_size % 8 == 0
    )


def root_kind(data: bytes) -> str:
    if _valid_primary_gpt_header(data):
        return "raw-gpt-disk-header"
    if data.startswith(b"LUKS\xba\xbe"):
        return "luks-encrypted-volume"
    if len(data) >= 0x10048 and data[0x10040:0x10048] == b"_BHRfS_M":
        return "btrfs-filesystem"
    if len(data) >= 1082 and data[1080:1082] == b"\x53\xef":
        return "ext-filesystem"
    if data.startswith(b"XFSB"):
        return "xfs-filesystem"
    return "unknown"


def efi_kind(data: bytes) -> str:
    if _valid_primary_gpt_header(data):
        return "raw-gpt-disk-header"
    if len(data) < 512 or data[510:512] != b"\x55\xaa":
        return "unknown"
    sector_bytes = int.from_bytes(data[11:13], "little")
    sectors_per_cluster = data[13]
    reserved_sectors = int.from_bytes(data[14:16], "little")
    plausible_bpb = (
        sector_bytes in (512, 1024, 2048, 4096)
        and sectors_per_cluster in (1, 2, 4, 8, 16, 32, 64, 128)
        and reserved_sectors > 0
    )
    if plausible_bpb and data[82:90] == b"FAT32   ":
        return "fat32-partition-header"
    if plausible_bpb and data[54:62] in (b"FAT16   ", b"FAT12   "):
        return "fat16-or-fat12-partition-header"
    return "unknown"


def inspect(media_root: Path, root_image: Path, efi_image: Path) -> dict:
    result = {
        "schema": "x1.offline-root-efi-structure.v1",
        "read_only": True,
        "off_host_media_required": True,
        "mount_or_restore_performed": False,
        "boot_rehearsal_performed": False,
        "independent_backup_custody_proven": False,
        "production_change_permitted": False,
        "decision": "HOLD",
    }
    try:
        if (not media_root.is_absolute() or media_root.is_symlink()
                or media_root == Path("/")
                or media_root.parts[1:2] in {
                    ("dev",), ("proc",), ("sys",), ("run",), ("etc",), ("boot",)
                }
                or root_image == efi_image):
            raise ValueError("invalid offline image selection")
        root = _prefix(media_root, root_image)
        efi = _prefix(media_root, efi_image)
        root_format = root_kind(root)
        efi_format = efi_kind(efi)
        result.update({
            "root_header_class": root_format,
            "efi_header_class": efi_format,
            "recognized_root_header": root_format != "unknown",
            "recognized_efi_header": efi_format != "unknown",
            "observation_complete": True,
        })
    except (OSError, ValueError, RuntimeError) as exc:
        result["observation_complete"] = False
        result["error_type"] = type(exc).__name__
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--explicit-off-host-read-only", action="store_true", required=True)
    p.add_argument("--media-root", required=True, type=Path)
    p.add_argument("--root-image", required=True, type=Path)
    p.add_argument("--efi-image", required=True, type=Path)
    args = p.parse_args(argv)
    print(json.dumps(
        inspect(args.media_root, args.root_image, args.efi_image),
        sort_keys=True,
    ))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
