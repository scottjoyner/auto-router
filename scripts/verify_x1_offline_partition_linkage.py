#!/usr/bin/env python3
"""Read-only proof that signed root/EFI bytes match GPT disk partition extents.

Used *after* v2 verifies all three image SHA-256 digests and the single
Ed25519 manifest. Exact partition content matches strengthen artifact linkage
but do NOT establish independent capture time, source-host identity or a bootable
restored OS. All paths are externally selected offline regular files.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from scripts.verify_x1_offline_gpt_mirror import (
    _ESP_TYPE_GUID, _read_disk, _u32, _u64, _valid_mirror,
)
from scripts.verify_x1_offline_recovery_images import (
    MAX_IMAGE_BYTES, _open_directory_nofollow, _under,
)

CHUNK = 4 * 1024 * 1024
MAX_MATCH_CANDIDATES = 4


def _digest_ranges(media_root: Path, disk_image: Path,
                   candidates: list[tuple[int, int]],
                   expected_size: int) -> list[str]:
    """Hash partition extents without opening a block device or following links."""
    if not _under(media_root, disk_image):
        raise ValueError("not an offline image path")
    parent = _open_directory_nofollow(media_root)
    parts = disk_image.relative_to(media_root).parts
    try:
        for component in parts[:-1]:
            nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY |
                          os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = nxt
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW |
                     getattr(os, "O_NONBLOCK", 0), dir_fd=parent)
    finally:
        os.close(parent)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= MAX_IMAGE_BYTES):
            raise ValueError("disk image is not a single regular file")
        digests = []
        for start, length in candidates:
            if (length != expected_size or start < 0
                    or start + length > before.st_size):
                raise ValueError("invalid signed partition extent")
            h = hashlib.sha256()
            remaining = length
            offset = start
            while remaining:
                chunk = os.pread(fd, min(CHUNK, remaining), offset)
                if not chunk:
                    raise ValueError("partition extent truncated")
                h.update(chunk)
                remaining -= len(chunk)
                offset += len(chunk)
            digests.append(h.hexdigest())
        after = os.fstat(fd)
        if any((
            before.st_dev != after.st_dev,
            before.st_ino != after.st_ino,
            before.st_size != after.st_size,
            before.st_mtime_ns != after.st_mtime_ns,
            before.st_ctime_ns != after.st_ctime_ns,
        )):
            raise ValueError("offline image mutated while reading partition extents")
        return digests
    finally:
        os.close(fd)


def exact_partition_links(
    *, media_root: Path, disk_image: Path, root_sha256: str,
    root_bytes: int, efi_sha256: str, efi_bytes: int,
) -> bool:
    """Return True only if exactly one root and one ESP extent match their SHA.

    False means unproven, not that valid partition-based backups are corrupt.
    The signed root/EFI images must be raw byte-for-byte partition copies,
    not file-level archives, sparse-container formats or compressed exports.
    """
    if (not isinstance(root_sha256, str) or not isinstance(efi_sha256, str)
            or len(root_sha256) != 64 or len(efi_sha256) != 64
            or any(c not in "0123456789abcdef" for c in root_sha256 + efi_sha256)
            or type(root_bytes) is not int or type(efi_bytes) is not int
            or not 0 < root_bytes <= MAX_IMAGE_BYTES
            or not 0 < efi_bytes <= MAX_IMAGE_BYTES
            or root_bytes % 512 or efi_bytes % 512):
        return False
    primary, secondary, entries, size = _read_disk(media_root, disk_image)
    if not _valid_mirror(primary, secondary, entries, size):
        return False
    gpt = primary[512:1024]
    width = _u32(gpt, 84)
    root_candidates = []
    efi_candidates = []
    for i in range(0, len(entries), width):
        part = entries[i:i + width]
        if not any(part[:16]):
            continue
        first, last = _u64(part, 32), _u64(part, 40)
        part_length = (last - first + 1) * 512
        start = first * 512
        if part[:16] == _ESP_TYPE_GUID and part_length == efi_bytes:
            efi_candidates.append((start, part_length))
        if part[:16] != _ESP_TYPE_GUID and part_length == root_bytes:
            root_candidates.append((start, part_length))
    if (not efi_candidates or not root_candidates
            or len(efi_candidates) > MAX_MATCH_CANDIDATES
            or len(root_candidates) > MAX_MATCH_CANDIDATES):
        return False
    # A signature may cover all three files without proving the source image
    # contains their bytes. Test both content hashes at exact GPT locations.
    root_digests = _digest_ranges(
        media_root, disk_image, root_candidates, root_bytes,
    )
    efi_digests = _digest_ranges(
        media_root, disk_image, efi_candidates, efi_bytes,
    )
    return (
        sum(v == root_sha256 for v in root_digests) == 1
        and sum(v == efi_sha256 for v in efi_digests) == 1
    )
