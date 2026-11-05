#!/usr/bin/env python3
"""OFF-HOST, read-only checksum inspection of previously created x1 root+EFI images.

The operator supplies two independently recorded SHA-256 digests. A match
does not certify restore/boot, chain of custody, source identity or maintenance
approval. No image creation, disk/device reads, mounting or host operations.
Always returns nonzero: JSON findings are evidence-only, NEVER a release gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

HEX64 = re.compile(r"^[0-9a-f]{64}$")
CHUNK_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024**4


def _under(root: Path, path: Path) -> bool:
    # This is intentionally lexical. Every component is subsequently opened
    # with O_NOFOLLOW, so a symlink cannot silently redirect the read outside
    # the operator-selected media directory between resolve() and open().
    return (
        root.is_absolute()
        and path.is_absolute()
        and path != root
        and ".." not in root.parts
        and ".." not in path.parts
        and path.is_relative_to(root)
    )


def _open_directory_nofollow(path: Path) -> int:
    """Walk from / using held directory FDs, rejecting every symlink."""
    if not path.is_absolute() or ".." in path.parts or path == Path("/"):
        raise ValueError("invalid offline-media directory")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=parent)
            os.close(parent)
            parent = child
        return parent
    except BaseException:
        os.close(parent)
        raise


def sha256_regular_offline(path: Path, *, media_root: Path) -> tuple[str, int]:
    if not _under(media_root, path):
        raise ValueError("backup path is not contained in offline-media directory")
    components = path.relative_to(media_root).parts
    parent = _open_directory_nofollow(media_root)
    try:
        for component in components[:-1]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
            os.close(parent)
            parent = child
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(components[-1], flags, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        initial = os.fstat(fd)
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
            raise ValueError("backup must be a single regular, nonlinked file")
        if not (0 < initial.st_size <= MAX_IMAGE_BYTES):
            raise ValueError("backup image has implausible size")
        digest = hashlib.sha256()
        consumed = 0
        while True:
            block = os.read(fd, CHUNK_BYTES)
            if not block:
                break
            consumed += len(block)
            if consumed > initial.st_size or consumed > MAX_IMAGE_BYTES:
                raise ValueError("backup changed size during read")
            digest.update(block)
        after = os.fstat(fd)
        if (consumed != initial.st_size or after.st_ino != initial.st_ino
                or after.st_dev != initial.st_dev or after.st_size != initial.st_size
                or after.st_mtime_ns != initial.st_mtime_ns
                or after.st_ctime_ns != initial.st_ctime_ns):
            raise ValueError("backup changed while inspected")
        return digest.hexdigest(), consumed
    finally:
        os.close(fd)


def inspect(*, media_root: Path, root_image: Path, efi_image: Path,
            expected_root_sha: str, expected_efi_sha: str) -> dict:
    report = {
        "schema": "x1-offline-root-efi-image-inspection.v1",
        "read_only": True,
        "off_host_operator_selection_required": True,
        "read_device_nodes": False,
        "mount_or_restore_performed": False,
        "root_and_efi_restore_certified": False,
        "source_witness_signature_verified": False,
        "recovery_approval_granted": False,
        "production_change_permitted": False,
        "decision": "HOLD",
    }
    try:
        if (HEX64.fullmatch(expected_root_sha) is None
                or HEX64.fullmatch(expected_efi_sha) is None):
            raise ValueError("full lowercase pinned SHA-256 values required")
        if not media_root.is_absolute() or media_root.is_symlink():
            raise ValueError("explicit absolute offline media root required")
        # No root filesystem scan or reading /dev, /boot, runtime or sensitive
        # live host paths. The media location still needs independent human
        # verification as physically detached from the production host.
        if (media_root.resolve(strict=True) in {Path("/"), Path("/mnt"), Path("/home")}
                or media_root.resolve(strict=True).parts[1:2]
                    in {("dev",), ("proc",), ("sys",), ("run",), ("etc",), ("boot",)}):
            raise ValueError("unsafe offline media root")
        if root_image == efi_image:
            raise ValueError("root and EFI images cannot be the same file")
        root_sha, root_bytes = sha256_regular_offline(root_image, media_root=media_root)
        efi_sha, efi_bytes = sha256_regular_offline(efi_image, media_root=media_root)
        report["image_evidence"] = {
            "root": {"size_bytes": root_bytes,
                     "matches_independently_supplied_sha256": root_sha == expected_root_sha},
            "efi": {"size_bytes": efi_bytes,
                    "matches_independently_supplied_sha256": efi_sha == expected_efi_sha},
        }
        report["both_image_digests_match"] = (
            root_sha == expected_root_sha and efi_sha == expected_efi_sha
        )
        report["observation_complete"] = True
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        report["observation_complete"] = False
        report["both_image_digests_match"] = False
        report["error_type"] = type(exc).__name__
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated-offline-media-only", action="store_true",
                        required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--root-image", type=Path, required=True)
    parser.add_argument("--efi-image", type=Path, required=True)
    parser.add_argument("--expected-root-sha256", required=True)
    parser.add_argument("--expected-efi-sha256", required=True)
    args = parser.parse_args(argv)
    report = inspect(
        media_root=args.media_root, root_image=args.root_image,
        efi_image=args.efi_image, expected_root_sha=args.expected_root_sha256,
        expected_efi_sha=args.expected_efi_sha256,
    )
    print(json.dumps(report, sort_keys=True))
    # Even a checksum match is not a restore or boot rehearsal.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
