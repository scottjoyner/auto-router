#!/usr/bin/env python3
"""Combine actual signed root/EFI and whole-disk checks on OFF-HOST media.

Inputs must be independently acquired and pinned by an operator. This module
calls the existing read-only validators on the specified media; it does not
trust or merge attacker-written report files. It never mounts devices, writes,
repairs packages or boots a host. A passing structural pair is not an
independent provenance link between the two artifacts. Always HOLD / exit 2.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.verify_x1_recovery_witness import verify
from scripts.verify_x1_offline_gpt_mirror import inspect


def assess(*, media_root: Path, manifest: Path, root_image: Path,
           efi_image: Path, disk_image: Path, signature_hex: str,
           trusted_public_key_hex: str, high_water_generation: int) -> dict:
    signed_pair = verify(
        media_root=media_root, manifest=manifest, root_image=root_image,
        efi_image=efi_image, signature_hex=signature_hex,
        trusted_public_key_hex=trusted_public_key_hex,
        high_water_generation=high_water_generation,
    )
    disk = inspect(media_root, disk_image)
    signed_checks = (
        signed_pair.get("signature_verified_for_supplied_public_key") is True
        and signed_pair.get("generation_floor_satisfied") is True
        and signed_pair.get("root_efi_signed_digests_and_sizes_match") is True
        and signed_pair.get("observation_complete") is True
    )
    structural_checks = (
        disk.get("primary_backup_gpt_and_array_match") is True
        and disk.get("observation_complete") is True
    )
    checklist = {
        "root_efi_signature_highwater_and_hashes_valid": signed_checks,
        "whole_disk_primary_backup_gpt_semantics_valid": structural_checks,
        # No part of the independent signed root/EFI manifest includes a
        # whole-disk hash. Do NOT equate this with an associated disk backup.
        "whole_disk_linked_to_signed_root_efi_pair": False,
        "signing_key_custody_independently_proven": False,
        "tested_clone_restored_on_independent_media": False,
        "uefi_console_boot_to_verified_userspace_witnessed": False,
        "rollback_and_out_of_band_access_witnessed": False,
        "production_host_maintenance_approved": False,
    }
    return {
        "schema": "x1.offline-recovery-gate-summary.v1",
        "host": "x1-370",
        "read_only": True,
        "signed_root_efi_observation_complete": (
            signed_pair.get("observation_complete") is True
        ),
        "raw_disk_observation_complete": disk.get("observation_complete") is True,
        "checks": checklist,
        "unmet_evidence": [
            key for key, passed in checklist.items() if not passed
        ],
        "production_repair_or_reboot_permitted": False,
        "decision": "HOLD",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explicit-independent-offhost-readonly",
                        action="store_true", required=True)
    parser.add_argument("--media-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--root-image", required=True, type=Path)
    parser.add_argument("--efi-image", required=True, type=Path)
    parser.add_argument("--disk-image", required=True, type=Path)
    parser.add_argument("--signature-hex", required=True)
    parser.add_argument("--trusted-public-key-hex", required=True)
    parser.add_argument("--high-water-generation", required=True, type=int)
    args = parser.parse_args(argv)
    report = assess(
        media_root=args.media_root, manifest=args.manifest,
        root_image=args.root_image, efi_image=args.efi_image,
        disk_image=args.disk_image, signature_hex=args.signature_hex,
        trusted_public_key_hex=args.trusted_public_key_hex,
        high_water_generation=args.high_water_generation,
    )
    # Only boolean signals and fixed names, no paths/keys/signatures/digests.
    print(json.dumps(report, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
