#!/usr/bin/env python3
"""Compare independently restored OFFLINE raw clone to a signed source disk.

This does not restore a disk, mount a filesystem, boot firmware or write data.
The signed source digest and size MUST first come from an independently
authenticated v2 bundle; the operator supplies them here, not the application.
Even a byte-exact clone does not establish bootability or grant host repair.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from scripts.verify_x1_offline_gpt_mirror import inspect as inspect_gpt
from scripts.verify_x1_offline_recovery_images import (
    MAX_IMAGE_BYTES, _open_directory_nofollow, _under,
)

DIGEST = re.compile(r"^[0-9a-f]{64}$")
CHUNK = 4 * 1024 * 1024


def _stream_sha256(media_root: Path, image: Path, expected_size: int) -> str:
    if not _under(media_root, image):
        raise ValueError("clone not under offline media root")
    parts = image.relative_to(media_root).parts
    parent = _open_directory_nofollow(media_root)
    try:
        for part in parts[:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
            os.close(parent)
            parent = child
        fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
    finally:
        os.close(parent)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size != expected_size):
            raise ValueError("offline clone size or regular-file constraint failed")
        digest = hashlib.sha256()
        count = 0
        while count < expected_size:
            chunk = os.read(fd, min(CHUNK, expected_size - count))
            if not chunk:
                raise ValueError("truncated clone")
            digest.update(chunk)
            count += len(chunk)
        after = os.fstat(fd)
        if (before.st_dev != after.st_dev or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns):
            raise ValueError("clone changed while reading")
        return digest.hexdigest()
    finally:
        os.close(fd)


def inspect_clone(*, media_root: Path, clone_image: Path,
                  authenticated_disk_sha256: str,
                  authenticated_disk_bytes: int) -> dict:
    result = {
        "schema": "x1.offline-clone-copy-check.v1",
        "read_only": True,
        "clone_size_and_sha256_match_pinned_source": False,
        "clone_gpt_structurally_consistent": False,
        "independent_source_bundle_authentication_verified_here": False,
        "physical_restoration_proven": False,
        "firmware_boot_and_os_userspace_proven": False,
        "rollback_and_out_of_band_console_proven": False,
        "live_x1_maintenance_authorized": False,
        "decision": "HOLD",
    }
    try:
        if (not media_root.is_absolute() or media_root == Path("/")
                or media_root.is_symlink()
                or media_root.parts[1:2] in {
                    ("dev",), ("proc",), ("sys",), ("run",), ("etc",), ("boot",)
                }
                or type(authenticated_disk_bytes) is not int
                or not 1024 * 1024 <= authenticated_disk_bytes <= MAX_IMAGE_BYTES
                or authenticated_disk_bytes % 512
                or type(authenticated_disk_sha256) is not str
                or not DIGEST.fullmatch(authenticated_disk_sha256)):
            raise ValueError("pinned offline evidence invalid")
        observed_sha = _stream_sha256(
            media_root, clone_image, authenticated_disk_bytes,
        )
        result["clone_size_and_sha256_match_pinned_source"] = (
            observed_sha == authenticated_disk_sha256
        )
        if result["clone_size_and_sha256_match_pinned_source"]:
            proof = inspect_gpt(media_root, clone_image)
            result["clone_gpt_structurally_consistent"] = (
                proof.get("observation_complete") is True
                and proof.get("primary_backup_gpt_and_array_match") is True
            )
        result["observation_complete"] = True
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        result["observation_complete"] = False
        result["error_type"] = type(exc).__name__
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--explicit-independent-offhost-readonly",
                   action="store_true", required=True)
    p.add_argument("--media-root", required=True, type=Path)
    p.add_argument("--clone-image", required=True, type=Path)
    p.add_argument("--authenticated-disk-sha256", required=True)
    p.add_argument("--authenticated-disk-bytes", required=True, type=int)
    args = p.parse_args(argv)
    print(json.dumps(inspect_clone(
        media_root=args.media_root, clone_image=args.clone_image,
        authenticated_disk_sha256=args.authenticated_disk_sha256,
        authenticated_disk_bytes=args.authenticated_disk_bytes,
    ), sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
