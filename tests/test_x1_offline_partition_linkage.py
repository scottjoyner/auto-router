"""True raw partition linkage and independently signed but mismatched copies."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest

from scripts.verify_x1_offline_partition_linkage import (
    _LINUX_ROOT_TYPE_GUIDS, exact_partition_links,
)
from scripts.verify_x1_offline_recovery_bundle_v2 import verify_bundle

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "partition_fixture", ROOT / "tests" / "test_x1_offline_recovery_bundle_v2.py",
)
assert spec and spec.loader
fixture_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_mod)

GPT_SPEC = importlib.util.spec_from_file_location(
    "gpt_fixture", ROOT / "tests" / "test_x1_offline_gpt_mirror.py",
)
assert GPT_SPEC and GPT_SPEC.loader
gpt = importlib.util.module_from_spec(GPT_SPEC)
GPT_SPEC.loader.exec_module(gpt)

EFI_FIRST, EFI_LAST = 34, 100
ROOT_FIRST, ROOT_LAST = 101, 130


def _aligned_case(base: Path, *, root_type: bytes | None = None):
    fixture = fixture_mod._case(base)
    media, manifest, root, efi, disk, meta, private, public = fixture
    image = bytearray(disk.read_bytes())
    second = 1024 + 128
    image[second:second + 16] = root_type or bytes.fromhex(
        "af3dc60f838472478e793d69d8477de4"
    )
    image[second + 16:second + 32] = bytes(range(33, 49))
    image[second + 32:second + 40] = ROOT_FIRST.to_bytes(8, "little")
    image[second + 40:second + 48] = ROOT_LAST.to_bytes(8, "little")
    root_bytes = b"R" * ((ROOT_LAST - ROOT_FIRST + 1) * 512)
    efi_bytes = b"E" * ((EFI_LAST - EFI_FIRST + 1) * 512)
    image[ROOT_FIRST * 512:(ROOT_LAST + 1) * 512] = root_bytes
    image[EFI_FIRST * 512:(EFI_LAST + 1) * 512] = efi_bytes
    gpt.rehash_both_gpt_headers(image)
    root.write_bytes(root_bytes)
    efi.write_bytes(efi_bytes)
    disk.write_bytes(image)
    for label, path in (("root", root), ("efi", efi), ("disk", disk)):
        data = path.read_bytes()
        meta[label + "_sha256"] = hashlib.sha256(data).hexdigest()
        meta[label + "_bytes"] = len(data)
    manifest.write_bytes(fixture_mod._canonical(meta))
    return fixture


def _run(fixture, *, verify_extents=True):
    media, manifest, root, efi, disk, meta, private, public = fixture
    return verify_bundle(
        media_root=media, manifest=manifest, root_image=root,
        efi_image=efi, disk_image=disk,
        signature_hex=private.sign(manifest.read_bytes()).hex(),
        trusted_public_key_hex=public, high_water_generation=42,
        verify_extents=verify_extents,
    )


def test_signed_raw_partition_copies_match_exact_whole_disk_extents():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _aligned_case(Path(tmp))
        report = _run(fixture)
        assert report["single_manifest_signature_verified_for_supplied_key"]
        assert report["all_three_image_digests_and_sizes_match_signed_manifest"]
        assert report["whole_disk_primary_backup_gpt_consistent"]
        assert report["exact_partition_extent_check_requested"]
        assert report["signed_root_efi_exact_partition_extents_match_disk"]
        assert not report["captured_same_source_snapshot_independently_proven"]
        assert not report["production_repair_or_reboot_permitted"]
        assert report["decision"] == "HOLD"


@pytest.mark.parametrize("linux_root_type", [
    bytes.fromhex("af3dc60f838472478e793d69d8477de4"),
    bytes.fromhex("e3bc684fcde8b14d96e7fbcaf984b709"),
])
def test_both_supported_linux_partition_guid_formats_match_only_structurally(
    linux_root_type,
):
    # The GPT type is a byte-order-sensitive signal; neither listed type
    # independently proves that the host actually booted that filesystem.
    assert linux_root_type in _LINUX_ROOT_TYPE_GUIDS
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _aligned_case(Path(tmp), root_type=linux_root_type)
        report = _run(fixture)
        assert report["all_three_image_digests_and_sizes_match_signed_manifest"]
        assert report["signed_root_efi_exact_partition_extents_match_disk"]
        assert not report["captured_same_source_snapshot_independently_proven"]
        assert not report["physical_clone_restore_and_uefi_boot_witnessed"]
        assert not report["production_repair_or_reboot_permitted"]
        assert report["decision"] == "HOLD"


@pytest.mark.parametrize("unsupported_root_type", [
    bytes.fromhex("0102030405060708090a0b0c0d0e0f10"),
    bytes.fromhex("7900deca66c7b34c9ca2a3c0686b7267"),  # Linux LVM GUID
])
def test_valid_signed_nonroot_or_unsupported_layout_fails_closed(
    unsupported_root_type,
):
    assert unsupported_root_type not in _LINUX_ROOT_TYPE_GUIDS
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _aligned_case(Path(tmp), root_type=unsupported_root_type)
        report = _run(fixture)
        assert report["single_manifest_signature_verified_for_supplied_key"]
        assert report["all_three_image_digests_and_sizes_match_signed_manifest"]
        assert report["whole_disk_primary_backup_gpt_consistent"]
        assert not report["signed_root_efi_exact_partition_extents_match_disk"]
        assert not report["production_repair_or_reboot_permitted"]
        assert report["decision"] == "HOLD"


def test_signed_matching_bytes_in_nonlinux_partition_cannot_impersonate_root():
    # A fully consistent triple-signed backup can still place the root-image
    # bytes in a Microsoft/unknown GPT partition rather than a Linux root.
    # The exact byte match must not become root-identity evidence.
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _aligned_case(Path(tmp))
        media, manifest, root, efi, disk, meta, private, public = fixture
        data = bytearray(disk.read_bytes())
        second = 1024 + 128
        # Random, structurally legal, non-Linux GPT type GUID.
        bogus = bytes.fromhex("0102030405060708090a0b0c0d0e0f10")
        assert bogus not in _LINUX_ROOT_TYPE_GUIDS
        data[second:second + 16] = bogus
        gpt.rehash_both_gpt_headers(data)
        disk.write_bytes(data)
        meta["disk_sha256"] = hashlib.sha256(data).hexdigest()
        manifest.write_bytes(fixture_mod._canonical(meta))
        report = _run(fixture)
        assert report["all_three_image_digests_and_sizes_match_signed_manifest"]
        assert report["whole_disk_primary_backup_gpt_consistent"]
        assert not report["signed_root_efi_exact_partition_extents_match_disk"]
        assert not report["production_repair_or_reboot_permitted"]
        assert report["decision"] == "HOLD"


def test_independently_signed_but_inconsistent_raw_partition_is_exposed():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _aligned_case(Path(tmp))
        media, manifest, root, efi, disk, meta, private, public = fixture
        # A signer can sign three perfectly valid but inconsistent files:
        # triple signed SHA-256 + GPT checksum alone cannot detect this.
        root.write_bytes(b"X" + root.read_bytes()[1:])
        meta["root_sha256"] = hashlib.sha256(root.read_bytes()).hexdigest()
        manifest.write_bytes(fixture_mod._canonical(meta))
        report = _run(fixture)
        assert report["all_three_image_digests_and_sizes_match_signed_manifest"]
        assert report["whole_disk_primary_backup_gpt_consistent"]
        assert not report["signed_root_efi_exact_partition_extents_match_disk"]
        assert report["decision"] == "HOLD"


def test_default_skips_expensive_additional_disk_reads():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _aligned_case(Path(tmp))
        report = _run(fixture, verify_extents=False)
        assert report["all_three_image_digests_and_sizes_match_signed_manifest"]
        assert not report["exact_partition_extent_check_requested"]
        assert not report["signed_root_efi_exact_partition_extents_match_disk"]


def test_directory_link_or_missing_root_signature_never_passes():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _aligned_case(Path(tmp))
        media, manifest, root, efi, disk, meta, private, public = fixture
        symlink = media / "symlink.raw"
        symlink.symlink_to(disk)
        with pytest.raises((ValueError, OSError)):
            exact_partition_links(
                media_root=media, disk_image=symlink,
                root_sha256=meta["root_sha256"], root_bytes=meta["root_bytes"],
                efi_sha256=meta["efi_sha256"], efi_bytes=meta["efi_bytes"],
            )
        assert not exact_partition_links(
            media_root=media, disk_image=disk,
            root_sha256="x" * 64, root_bytes=meta["root_bytes"],
            efi_sha256=meta["efi_sha256"], efi_bytes=meta["efi_bytes"],
        )


def test_wrong_size_signed_partition_cannot_pass():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _aligned_case(Path(tmp))
        media, manifest, root, efi, disk, meta, private, public = fixture
        assert not exact_partition_links(
            media_root=media, disk_image=disk,
            root_sha256=meta["root_sha256"], root_bytes=meta["root_bytes"] + 512,
            efi_sha256=meta["efi_sha256"], efi_bytes=meta["efi_bytes"],
        )
