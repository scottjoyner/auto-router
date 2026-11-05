#!/usr/bin/env python3
"""Verify a single Ed25519-signed root+EFI+whole-disk off-host backup manifest.

The older v1 signed root/EFI pair does NOT authenticate a separate whole-disk
image. This opt-in v2 format cryptographically binds all three exact image
digests, sizes, host identity, backup-set ID and generation to ONE detached
signature. It cannot independently prove that those files were captured from
the same source snapshot, or that the signer/key custody is authoritative.

OFF-HOST read-only evidence only. No disks under /dev, mounts, repair, restore,
reboot, writes, credential lookups or service calls. ALWAYS exit 2 / HOLD.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from scripts.verify_x1_recovery_witness import (
    BACKUP_SET_ID, SHA256, SIGNATURE, _distinct_object, _read_manifest,
)
from scripts.verify_x1_offline_recovery_images import (
    MAX_IMAGE_BYTES, sha256_regular_offline,
)
from scripts.verify_x1_offline_gpt_mirror import inspect as inspect_mirror
from scripts.verify_x1_offline_partition_linkage import exact_partition_links

SCHEMA = "x1.root-efi-disk-custody-bundle.v2"
FIELDS = {
    "schema", "host_id", "backup_set_id", "generation",
    "root_sha256", "root_bytes", "efi_sha256", "efi_bytes",
    "disk_sha256", "disk_bytes",
}


def verify_bundle(
    *, media_root: Path, manifest: Path, root_image: Path,
    efi_image: Path, disk_image: Path, signature_hex: str,
    trusted_public_key_hex: str, high_water_generation: int,
    verify_extents: bool = False,
) -> dict:
    report = {
        "schema": "x1.offline-signed-recovery-bundle-v2.v1",
        "read_only": True,
        "single_manifest_signature_verified_for_supplied_key": False,
        "generation_floor_satisfied": False,
        "all_three_image_digests_and_sizes_match_signed_manifest": False,
        "whole_disk_primary_backup_gpt_consistent": False,
        "signed_root_efi_exact_partition_extents_match_disk": False,
        "exact_partition_extent_check_requested": verify_extents is True,
        "independent_signer_key_custody_verified": False,
        "captured_same_source_snapshot_independently_proven": False,
        "physical_clone_restore_and_uefi_boot_witnessed": False,
        "out_of_band_console_and_rollback_verified": False,
        "production_repair_or_reboot_permitted": False,
        "decision": "HOLD",
    }
    try:
        if (type(high_water_generation) is not int or high_water_generation < 1
                or not isinstance(signature_hex, str)
                or SIGNATURE.fullmatch(signature_hex) is None
                or not isinstance(trusted_public_key_hex, str)
                or SHA256.fullmatch(trusted_public_key_hex) is None
                or len({root_image, efi_image, disk_image, manifest}) != 4
                or not media_root.is_absolute()
                or media_root == Path("/")
                or media_root.is_symlink()
                or media_root.parts[1:2] in {
                    ("dev",), ("proc",), ("sys",), ("run",), ("etc",), ("boot",),
                }):
            raise ValueError("independent offline evidence selection invalid")
        raw = _read_manifest(media_root, manifest)
        witness = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_distinct_object,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("nonfinite manifest JSON"),
            ),
        )
        if (type(witness) is not dict or set(witness) != FIELDS
                or witness.get("schema") != SCHEMA
                or witness.get("host_id") != "x1-370"
                or type(witness.get("backup_set_id")) is not str
                or BACKUP_SET_ID.fullmatch(witness["backup_set_id"]) is None
                or type(witness.get("generation")) is not int
                or witness["generation"] < 1
                or any(
                    type(witness[k]) is not int
                    or not 0 < witness[k] <= MAX_IMAGE_BYTES
                    for k in ("root_bytes", "efi_bytes", "disk_bytes")
                )
                or witness["disk_bytes"] < 1024 * 1024
                or witness["disk_bytes"] % 512 != 0
                or any(
                    type(witness[k]) is not str
                    or SHA256.fullmatch(witness[k]) is None
                    for k in ("root_sha256", "efi_sha256", "disk_sha256")
                )):
            raise ValueError("unexpected or invalid signed bundle metadata")
        canonical = json.dumps(
            witness, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("utf-8")
        if canonical != raw:
            raise ValueError("noncanonical manifest encoding")
        try:
            Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(trusted_public_key_hex)
            ).verify(bytes.fromhex(signature_hex), raw)
        except (InvalidSignature, ValueError) as exc:
            raise ValueError("bundle signature invalid") from exc
        report["single_manifest_signature_verified_for_supplied_key"] = True
        if witness["generation"] < high_water_generation:
            raise ValueError("signed generation below independent floor")
        report["generation_floor_satisfied"] = True
        image_triplet = (
            ("root", root_image), ("efi", efi_image), ("disk", disk_image),
        )
        images_match = True
        for label, image in image_triplet:
            actual_digest, actual_size = sha256_regular_offline(
                image, media_root=media_root,
            )
            if (actual_digest != witness[label + "_sha256"]
                    or actual_size != witness[label + "_bytes"]):
                images_match = False
        report["all_three_image_digests_and_sizes_match_signed_manifest"] = (
            images_match
        )
        if images_match:
            mirror = inspect_mirror(media_root, disk_image)
            mirror_ok = (
                mirror.get("observation_complete") is True
                and mirror.get("primary_backup_gpt_and_array_match") is True
            )
            report["whole_disk_primary_backup_gpt_consistent"] = mirror_ok
            if mirror_ok and verify_extents is True:
                # Optional additional whole-disk I/O on detached media only.
                # This detects independently signed but inconsistent raw
                # partition copies without inventing a source-capture witness.
                report["signed_root_efi_exact_partition_extents_match_disk"] = (
                    exact_partition_links(
                        media_root=media_root, disk_image=disk_image,
                        root_sha256=witness["root_sha256"],
                        root_bytes=witness["root_bytes"],
                        efi_sha256=witness["efi_sha256"],
                        efi_bytes=witness["efi_bytes"],
                    )
                )
        report["observation_complete"] = True
    except (OSError, ValueError, TypeError, UnicodeError,
            json.JSONDecodeError, RuntimeError) as exc:
        report["observation_complete"] = False
        report["error_type"] = type(exc).__name__
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--explicit-independent-offhost-readonly",
        action="store_true", required=True,
    )
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root-image", type=Path, required=True)
    parser.add_argument("--efi-image", type=Path, required=True)
    parser.add_argument("--disk-image", type=Path, required=True)
    parser.add_argument("--signature-hex", required=True)
    parser.add_argument("--trusted-public-key-hex", required=True)
    parser.add_argument("--high-water-generation", type=int, required=True)
    parser.add_argument("--verify-exact-partition-extents",
                        action="store_true")
    args = parser.parse_args(argv)
    report = verify_bundle(
        media_root=args.media_root, manifest=args.manifest,
        root_image=args.root_image, efi_image=args.efi_image,
        disk_image=args.disk_image, signature_hex=args.signature_hex,
        trusted_public_key_hex=args.trusted_public_key_hex,
        high_water_generation=args.high_water_generation,
        verify_extents=args.verify_exact_partition_extents,
    )
    print(json.dumps(report, sort_keys=True))
    return 2  # No signature or structural classification authorizes repair.


if __name__ == "__main__":
    raise SystemExit(main())
