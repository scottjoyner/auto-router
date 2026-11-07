"""Synthetic Ed25519 off-host root+EFI custody proof: always HOLD."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_x1_recovery_witness.py"
spec = importlib.util.spec_from_file_location("verify_x1_recovery_witness", MODULE_PATH)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def signed_fixture(base: Path):
    media = base / "offline-media"
    media.mkdir()
    root = media / "root.img"
    efi = media / "efi.img"
    root.write_bytes(b"fixture-root-image" * 200)
    efi.write_bytes(b"fixture-efi-image" * 50)
    witness = {
        "schema": mod.MANIFEST_SCHEMA,
        "host_id": "x1-370",
        "backup_set_id": "synthetic-backup-set-0001",
        "generation": 12,
        "root_sha256": hashlib.sha256(root.read_bytes()).hexdigest(),
        "efi_sha256": hashlib.sha256(efi.read_bytes()).hexdigest(),
        "root_bytes": root.stat().st_size,
        "efi_bytes": efi.stat().st_size,
    }
    manifest = media / "witness.json"
    private = Ed25519PrivateKey.generate()  # Synthetic only; never saved.
    key = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    raw = json.dumps(
        witness, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    manifest.write_bytes(raw)
    sig = private.sign(raw).hex()
    return media, root, efi, manifest, witness, private, key, sig


def verify(media, root, efi, manifest, key, sig, high_water=12):
    return mod.verify(
        media_root=media, root_image=root, efi_image=efi,
        manifest=manifest, signature_hex=sig, trusted_public_key_hex=key,
        high_water_generation=high_water,
    )


class WitnessTests(unittest.TestCase):
    def test_signed_pair_and_external_floor_are_evidence_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = signed_fixture(Path(tmp))
            media, root, efi, manifest, _, _, key, sig = fixture
            result = verify(media, root, efi, manifest, key, sig)
            self.assertTrue(result["signature_verified_for_supplied_public_key"])
            self.assertTrue(result["generation_floor_satisfied"])
            self.assertTrue(result["root_efi_signed_digests_and_sizes_match"])
            self.assertTrue(result["observation_complete"])
            self.assertFalse(result["independent_key_custody_verified"])
            self.assertFalse(result["restore_or_boot_rehearsed"])
            self.assertFalse(result["production_repair_or_reboot_permitted"])
            self.assertEqual(result["gate"], "HOLD")
            self.assertNotIn(str(media), json.dumps(result))
            self.assertEqual(mod.main([
                "--independently-witnessed-offline-only",
                "--media-root", str(media), "--manifest", str(manifest),
                "--root-image", str(root), "--efi-image", str(efi),
                "--signature-hex", sig,
                "--trusted-public-key-hex", key,
                "--high-water-generation", "12",
            ]), 2)

    def test_forged_signature_and_untrusted_public_key_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            media, root, efi, manifest, _, private, key, sig = signed_fixture(Path(tmp))
            other = Ed25519PrivateKey.generate().public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            ).hex()
            for candidate_key, candidate_sig in (
                (other, sig), (key, ("0" if sig[0] != "0" else "1") + sig[1:]),
                (key, "bad"), (key[:-1], sig),
            ):
                with self.subTest(key=candidate_key):
                    report = verify(
                        media, root, efi, manifest, candidate_key, candidate_sig,
                    )
                    self.assertFalse(report["observation_complete"])
                    self.assertFalse(report["signature_verified_for_supplied_public_key"])

    def test_external_generation_floor_detects_old_signed_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            media, root, efi, manifest, _, _, key, sig = signed_fixture(Path(tmp))
            report = verify(media, root, efi, manifest, key, sig, high_water=13)
            self.assertTrue(report["signature_verified_for_supplied_public_key"])
            self.assertFalse(report["generation_floor_satisfied"])
            self.assertFalse(report["root_efi_signed_digests_and_sizes_match"])
            self.assertEqual(report["gate"], "HOLD")
            for floor in (0, -1, True):
                report = verify(media, root, efi, manifest, key, sig, high_water=floor)
                self.assertFalse(report["observation_complete"])

    def test_changed_images_do_not_match_signed_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            media, root, efi, manifest, _, _, key, sig = signed_fixture(Path(tmp))
            efi.write_bytes(efi.read_bytes() + b"changed")
            report = verify(media, root, efi, manifest, key, sig)
            self.assertTrue(report["signature_verified_for_supplied_public_key"])
            self.assertFalse(report["root_efi_signed_digests_and_sizes_match"])
            self.assertTrue(report["observation_complete"])

    def test_manifest_symlink_and_noncanonical_or_duplicate_json_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            media, root, efi, manifest, witness, private, key, sig = signed_fixture(Path(tmp))
            alias = media / "alias.json"
            alias.symlink_to(manifest)
            report = verify(media, root, efi, alias, key, sig)
            self.assertFalse(report["observation_complete"])
            raw = json.dumps(witness, sort_keys=True, indent=2).encode("utf-8")
            manifest.write_bytes(raw)
            revised = private.sign(raw).hex()
            report = verify(media, root, efi, manifest, key, revised)
            self.assertFalse(report["observation_complete"])
            canonical = json.dumps(
                witness, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            doubled = canonical[:-1] + b',"host_id":"x1-370"}'
            manifest.write_bytes(doubled)
            report = verify(media, root, efi, manifest, key, private.sign(doubled).hex())
            self.assertFalse(report["observation_complete"])

    def test_wrong_host_unknown_field_and_wrong_image_size_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            media, root, efi, manifest, witness, private, key, _ = signed_fixture(Path(tmp))
            for mutation in (
                {"host_id": "other-host"},
                {"unknown": "unexpected"},
                {"root_bytes": 0},
                {"root_bytes": True},
                {"generation": 12.5},
                {"backup_set_id": "x"},
            ):
                with self.subTest(mutation=mutation):
                    altered = {**witness, **mutation}
                    raw = json.dumps(
                        altered, sort_keys=True, separators=(",", ":"),
                    ).encode("utf-8")
                    manifest.write_bytes(raw)
                    report = verify(media, root, efi, manifest, key, private.sign(raw).hex())
                    self.assertFalse(report["observation_complete"])
                    self.assertEqual(report["gate"], "HOLD")


if __name__ == "__main__":
    unittest.main()
