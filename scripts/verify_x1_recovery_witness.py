#!/usr/bin/env python3
"""Verify an externally signed root+EFI backup pair on isolated offline media.

Research/evidence only. The public key and generation floor MUST originate
from independent operator-controlled custody, not this x1 host or its backup.
No write, device access, mount, reboot or repair operations. Always exit 2.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from scripts.verify_x1_offline_recovery_images import (
    MAX_IMAGE_BYTES,
    _open_directory_nofollow,
    _under,
    sha256_regular_offline,
)

MANIFEST_SCHEMA = "x1.root-efi-custody-pair.v1"
REPORT_SCHEMA = "x1.offline-witness-verification.v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SIGNATURE = re.compile(r"^[0-9a-f]{128}$")
BACKUP_SET_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{15,63}$")
MAX_MANIFEST_BYTES = 4096


def _distinct_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate witness manifest key")
        result[key] = value
    return result


def _read_manifest(media_root: Path, manifest: Path) -> bytes:
    if not _under(media_root, manifest):
        raise ValueError("manifest must reside inside offline media directory")
    parts = manifest.relative_to(media_root).parts
    parent = _open_directory_nofollow(media_root)
    try:
        for segment in parts[:-1]:
            nxt = os.open(
                segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
            os.close(parent)
            parent = nxt
        fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
    finally:
        os.close(parent)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= MAX_MANIFEST_BYTES):
            raise ValueError("invalid witness manifest file")
        content = os.read(fd, MAX_MANIFEST_BYTES + 1)
        after = os.fstat(fd)
        if (len(content) != before.st_size or before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
                or before.st_size != after.st_size):
            raise ValueError("manifest changed during read")
        return content
    finally:
        os.close(fd)


def verify(*, media_root: Path, manifest: Path, root_image: Path,
           efi_image: Path, signature_hex: str, trusted_public_key_hex: str,
           high_water_generation: int) -> dict:
    report = {
        "schema": REPORT_SCHEMA,
        "read_only": True,
        "source": "operator-supplied isolated offline media",
        "signature_verified_for_supplied_public_key": False,
        "independent_key_custody_verified": False,
        "generation_floor_satisfied": False,
        "root_efi_signed_digests_and_sizes_match": False,
        "restore_or_boot_rehearsed": False,
        "production_repair_or_reboot_permitted": False,
        "gate": "HOLD",
    }
    try:
        if (type(high_water_generation) is not int or high_water_generation < 1
                or SIGNATURE.fullmatch(signature_hex) is None
                or SHA256.fullmatch(trusted_public_key_hex) is None):
            raise ValueError("trusted external signature/key/generation required")
        if (not media_root.is_absolute() or media_root.is_symlink()
                or media_root == Path("/")
                or media_root.parts[1:2] in {
                    ("dev",), ("proc",), ("sys",), ("run",), ("etc",), ("boot",)
                }):
            raise ValueError("unsafe offline media selection")
        if root_image == efi_image:
            raise ValueError("two distinct root and EFI images required")
        source = _read_manifest(media_root, manifest)
        witness = json.loads(
            source.decode("utf-8"), object_pairs_hook=_distinct_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")),
        )
        if (not isinstance(witness, dict) or
                set(witness) != {
                    "schema", "host_id", "backup_set_id", "generation",
                    "root_sha256", "root_bytes", "efi_sha256", "efi_bytes",
                } or witness["schema"] != MANIFEST_SCHEMA
                or witness["host_id"] != "x1-370"
                or not isinstance(witness["backup_set_id"], str)
                or BACKUP_SET_ID.fullmatch(witness["backup_set_id"]) is None
                or type(witness["generation"]) is not int
                or witness["generation"] < 1
                or any(
                    type(witness[k]) is not int or not 0 < witness[k] <= MAX_IMAGE_BYTES
                    for k in ("root_bytes", "efi_bytes")
                )
                or any(
                    not isinstance(witness[k], str) or SHA256.fullmatch(witness[k]) is None
                    for k in ("root_sha256", "efi_sha256")
                )):
            raise ValueError("invalid or unscoped witness manifest")
        canonical = json.dumps(
            witness, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        if source != canonical:
            raise ValueError("manifest must be canonical exact bytes")
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(
                trusted_public_key_hex
            )).verify(bytes.fromhex(signature_hex), source)
        except (InvalidSignature, ValueError) as exc:
            raise ValueError("detached signature not verified") from exc
        report["signature_verified_for_supplied_public_key"] = True
        report["generation_floor_satisfied"] = (
            witness["generation"] >= high_water_generation
        )
        # Check the independently pinned high-water mark before reading
        # potentially huge image files on the offline review machine.
        if not report["generation_floor_satisfied"]:
            raise ValueError("witness generation below independently supplied floor")
        root_sha, root_bytes = sha256_regular_offline(
            root_image, media_root=media_root,
        )
        efi_sha, efi_bytes = sha256_regular_offline(
            efi_image, media_root=media_root,
        )
        report["root_efi_signed_digests_and_sizes_match"] = (
            root_sha == witness["root_sha256"]
            and efi_sha == witness["efi_sha256"]
            and root_bytes == witness["root_bytes"]
            and efi_bytes == witness["efi_bytes"]
        )
        report["observation_complete"] = True
    except (OSError, ValueError, RuntimeError, TypeError,
            json.JSONDecodeError, UnicodeError) as exc:
        report["observation_complete"] = False
        report["error_type"] = type(exc).__name__
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--independently-witnessed-offline-only",
                   action="store_true", required=True)
    p.add_argument("--media-root", required=True, type=Path)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--root-image", required=True, type=Path)
    p.add_argument("--efi-image", required=True, type=Path)
    p.add_argument("--signature-hex", required=True)
    p.add_argument("--trusted-public-key-hex", required=True)
    p.add_argument("--high-water-generation", required=True, type=int)
    args = p.parse_args(argv)
    report = verify(
        media_root=args.media_root, manifest=args.manifest,
        root_image=args.root_image, efi_image=args.efi_image,
        signature_hex=args.signature_hex,
        trusted_public_key_hex=args.trusted_public_key_hex,
        high_water_generation=args.high_water_generation,
    )
    print(json.dumps(report, sort_keys=True))
    return 2  # Even cryptographic receipt validity is not a restore permit.


if __name__ == "__main__":
    raise SystemExit(main())
