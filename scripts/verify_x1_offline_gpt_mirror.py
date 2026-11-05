#!/usr/bin/env python3
"""Bounded offline cross-check of primary/backup GPT in a raw disk image.

Research-only: no block devices, mounts, fsck, edits, boots, snapshots or
operator credentials. A consistent GPT mirror is NOT a tested clone restore.
Always exit 2 / HOLD. Linux/POSIX regular file handles are required.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import zlib

from scripts.inspect_x1_offline_boot_structure import (
    PREFIX_BYTES,
    _valid_primary_gpt_header,
)
from scripts.verify_x1_offline_recovery_images import (
    MAX_IMAGE_BYTES,
    _open_directory_nofollow,
    _under,
)

SECTOR = 512
MAX_TABLE = 64 * 1024


def _u64(data: bytes, start: int) -> int:
    return int.from_bytes(data[start:start + 8], "little")


def _u32(data: bytes, start: int) -> int:
    return int.from_bytes(data[start:start + 4], "little")


def _valid_mirror(primary: bytes, last: bytes, backup_table: bytes,
                  total_bytes: int) -> bool:
    if (total_bytes < 1024 * 1024 or total_bytes % SECTOR
            or not _valid_primary_gpt_header(primary)
            or len(last) != SECTOR or last[:8] != b"EFI PART"):
        return False
    front = primary[512:1024]
    header_size = _u32(last, 12)
    if not 92 <= header_size <= SECTOR:
        return False
    clone = bytearray(last[:header_size])
    expected_crc = _u32(clone, 16)
    clone[16:20] = b"\0" * 4
    if zlib.crc32(clone) & 0xFFFFFFFF != expected_crc:
        return False
    table_bytes = _u32(front, 80) * _u32(front, 84)
    entry_bytes = _u32(last, 80) * _u32(last, 84)
    front_array_start = _u64(front, 72) * SECTOR
    front_array_end = front_array_start + table_bytes
    total_sectors = total_bytes // SECTOR
    last_current = _u64(last, 24)
    last_original = _u64(last, 32)
    back_array_lba = _u64(last, 72)
    last_usable = _u64(last, 48)
    front_last_usable = _u64(front, 48)
    if not (
        front[20:24] == last[20:24] == b"\0" * 4
        and 0 < table_bytes <= MAX_TABLE
        and table_bytes == entry_bytes == len(backup_table)
        and 1024 <= front_array_start
        and front_array_end <= len(primary)
        and _u64(front, 32) == last_current == total_sectors - 1
        and last_original == _u64(front, 24) == 1
        and _u64(front, 40) == _u64(last, 40)
        and front_last_usable == last_usable
        and front[56:72] == last[56:72]
        and front[80:88] == last[80:88]
        and front[88:92] == last[88:92]
        and back_array_lba > last_usable
        and back_array_lba + (table_bytes + SECTOR - 1) // SECTOR
        == last_current
        and back_array_lba > _u64(front, 40)
        and zlib.crc32(backup_table) & 0xFFFFFFFF == _u32(last, 88)
        and primary[front_array_start:front_array_end] == backup_table
    ):
        return False
    return True


def _read_disk(media_root: Path, image: Path) -> tuple[bytes, bytes, bytes, int]:
    if not _under(media_root, image):
        raise ValueError("offline disk image outside media root")
    folder = _open_directory_nofollow(media_root)
    components = image.relative_to(media_root).parts
    try:
        for part in components[:-1]:
            nested = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=folder,
            )
            os.close(folder)
            folder = nested
        fd = os.open(
            components[-1],
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=folder,
        )
    finally:
        os.close(folder)
    try:
        before = os.fstat(fd)
        size = before.st_size
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or size < 1024 * 1024 or size > MAX_IMAGE_BYTES
                or size % SECTOR):
            raise ValueError("expected aligned offline regular disk image")
        primary = os.pread(fd, PREFIX_BYTES, 0)
        if len(primary) < 1024 or not _valid_primary_gpt_header(primary):
            raise ValueError("invalid bounded primary GPT")
        front = primary[512:1024]
        count, entry_size = _u32(front, 80), _u32(front, 84)
        bytes_count = count * entry_size
        last = os.pread(fd, SECTOR, size - SECTOR)
        backup_start = _u64(last, 72) * SECTOR
        if (bytes_count > MAX_TABLE or bytes_count <= 0
                or backup_start < size // 2
                or backup_start + bytes_count > size - SECTOR):
            raise ValueError("invalid bounded backup table location")
        table = os.pread(fd, bytes_count, backup_start)
        after = os.fstat(fd)
        if (before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns):
            raise ValueError("offline image changed during inspection")
        return primary, last, table, size
    finally:
        os.close(fd)


def inspect(media_root: Path, image: Path) -> dict:
    report = {
        "schema": "x1.offline-gpt-mirror.v1",
        "read_only": True,
        "primary_backup_gpt_and_array_match": False,
        "signed_backup_custody_verified": False,
        "filesystem_restore_verified": False,
        "clone_boot_verified": False,
        "production_host_repair_allowed": False,
        "decision": "HOLD",
    }
    try:
        if (not media_root.is_absolute() or media_root == Path("/")
                or media_root.is_symlink()
                or media_root.parts[1:2] in {
                    ("dev",), ("proc",), ("sys",), ("run",), ("etc",), ("boot",)
                }):
            raise ValueError("unsafe offline media root")
        primary, last, table, size = _read_disk(media_root, image)
        report["primary_backup_gpt_and_array_match"] = _valid_mirror(
            primary, last, table, size,
        )
        report["observation_complete"] = True
    except (OSError, ValueError, RuntimeError) as exc:
        report["observation_complete"] = False
        report["error_type"] = type(exc).__name__
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explicit-offhost-readonly", action="store_true",
                        required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--disk-image", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(
        inspect(args.media_root, args.disk_image), sort_keys=True,
    ))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
