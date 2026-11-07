"""One-shot signed manifest → exact offline clone copy; never boot authority."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from scripts.verify_x1_offline_recovery_bundle_v2 import verify_bundle

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "signed_clone_fixture", ROOT / "tests" / "test_x1_offline_recovery_bundle_v2.py",
)
assert spec and spec.loader
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def _one_shot(f, clone, *, bad_signer=False):
    media, manifest, root, efi, disk, record, private, public = f
    signer = Ed25519PrivateKey.generate() if bad_signer else private
    return verify_bundle(
        media_root=media, manifest=manifest,
        root_image=root, efi_image=efi, disk_image=disk,
        signature_hex=signer.sign(manifest.read_bytes()).hex(),
        trusted_public_key_hex=public,
        high_water_generation=42,
        clone_image=clone,
    )


def test_actual_signed_manifest_derives_digest_for_offline_copy():
    with tempfile.TemporaryDirectory() as tmp:
        f = fixture._case(Path(tmp))
        media, manifest, root, efi, disk, *_ = f
        clone = media / "restored-copy.raw"
        clone.write_bytes(disk.read_bytes())
        report = _one_shot(f, clone)
        assert report["single_manifest_signature_verified_for_supplied_key"]
        assert report["all_three_image_digests_and_sizes_match_signed_manifest"]
        assert report["whole_disk_primary_backup_gpt_consistent"]
        assert report["authenticated_offhost_clone_check_requested"]
        assert report["clone_copy_matches_signed_disk_digest_and_gpt"]
        assert not report["independent_signer_key_custody_verified"]
        assert not report["independent_physical_clone_boot_witnessed"]
        assert not report["production_repair_or_reboot_permitted"]
        assert report["decision"] == "HOLD"


def test_signed_original_but_corrupted_clone_holds():
    with tempfile.TemporaryDirectory() as tmp:
        f = fixture._case(Path(tmp))
        media, manifest, root, efi, disk, *_ = f
        clone = media / "restored-copy.raw"
        raw = bytearray(disk.read_bytes())
        raw[150_000] ^= 0x01
        clone.write_bytes(raw)
        report = _one_shot(f, clone)
        assert report["single_manifest_signature_verified_for_supplied_key"]
        assert report["all_three_image_digests_and_sizes_match_signed_manifest"]
        assert not report["clone_copy_matches_signed_disk_digest_and_gpt"]
        assert report["decision"] == "HOLD"


def test_untrusted_signer_never_even_attempts_clone_inspection(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        f = fixture._case(Path(tmp))
        media, *_ = f
        clone = media / "restored-copy.raw"
        def forbidden(**_):
            pytest.fail("clone should not be inspected before valid signature")
        monkeypatch.setattr(
            "scripts.verify_x1_offline_recovery_bundle_v2.inspect_clone",
            forbidden,
        )
        report = _one_shot(f, clone, bad_signer=True)
        assert not report["single_manifest_signature_verified_for_supplied_key"]
        assert not report["clone_copy_matches_signed_disk_digest_and_gpt"]


def test_original_path_cannot_self_attest_as_separate_clone():
    with tempfile.TemporaryDirectory() as tmp:
        f = fixture._case(Path(tmp))
        report = _one_shot(f, f[4])
        assert not report["observation_complete"]
        assert not report["clone_copy_matches_signed_disk_digest_and_gpt"]
        assert report["decision"] == "HOLD"


def test_clone_path_symlink_cannot_pass_signed_bundle():
    with tempfile.TemporaryDirectory() as tmp:
        f = fixture._case(Path(tmp))
        media, manifest, root, efi, disk, *_ = f
        link = media / "malicious-alias.raw"
        link.symlink_to(disk)
        result = _one_shot(f, link)
        assert not result["clone_copy_matches_signed_disk_digest_and_gpt"]
        assert not result["independent_physical_clone_boot_witnessed"]


def test_clone_check_is_optional_and_never_proves_physical_boot():
    with tempfile.TemporaryDirectory() as tmp:
        f = fixture._case(Path(tmp))
        media, manifest, root, efi, disk, meta, private, public = f
        result = verify_bundle(
            media_root=media, manifest=manifest,
            root_image=root, efi_image=efi, disk_image=disk,
            signature_hex=private.sign(manifest.read_bytes()).hex(),
            trusted_public_key_hex=public, high_water_generation=42,
        )
        assert not result["authenticated_offhost_clone_check_requested"]
        assert not result["clone_copy_matches_signed_disk_digest_and_gpt"]
        assert not result["production_repair_or_reboot_permitted"]
