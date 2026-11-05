"""Offline synthetic image evidence only; no block devices or host packages."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_x1_offline_recovery_images.py"
spec = importlib.util.spec_from_file_location("x1_offline_images", PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def fixture(root: Path):
    media = root / "offline-copy"
    media.mkdir()
    image = media / "x1-root.img"
    efi = media / "x1-efi.img"
    image.write_bytes(b"synthetic-root-backup-not-a-live-disk" * 16)
    efi.write_bytes(b"synthetic-efi-backup" * 4)
    return media, image, efi


class OfflineImageEvidenceTests(unittest.TestCase):
    def test_matching_images_report_evidence_but_never_release_or_exit_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            media, root, efi = fixture(Path(temp))
            root_pin = hashlib.sha256(root.read_bytes()).hexdigest()
            efi_pin = hashlib.sha256(efi.read_bytes()).hexdigest()
            report = mod.inspect(
                media_root=media, root_image=root, efi_image=efi,
                expected_root_sha=root_pin, expected_efi_sha=efi_pin,
            )
            self.assertTrue(report["both_image_digests_match"])
            self.assertTrue(report["observation_complete"])
            self.assertEqual(report["decision"], "HOLD")
            self.assertFalse(report["root_and_efi_restore_certified"])
            self.assertFalse(report["source_witness_signature_verified"])
            self.assertFalse(report["production_change_permitted"])
            self.assertNotIn(str(media), json.dumps(report))
            output = io.StringIO()
            args = ["--isolated-offline-media-only", "--media-root", str(media),
                    "--root-image", str(root), "--efi-image", str(efi),
                    "--expected-root-sha256", root_pin,
                    "--expected-efi-sha256", efi_pin]
            with redirect_stdout(output):
                self.assertEqual(mod.main(args), 2)
            self.assertTrue(json.loads(output.getvalue())["both_image_digests_match"])

    def test_tampering_or_stale_pins_cannot_look_like_valid_root_and_efi(self):
        with tempfile.TemporaryDirectory() as temp:
            media, root, efi = fixture(Path(temp))
            pin_root = hashlib.sha256(root.read_bytes()).hexdigest()
            pin_efi = hashlib.sha256(efi.read_bytes()).hexdigest()
            efi.write_bytes(efi.read_bytes() + b"changed")
            report = mod.inspect(
                media_root=media, root_image=root, efi_image=efi,
                expected_root_sha=pin_root, expected_efi_sha=pin_efi,
            )
            self.assertTrue(report["observation_complete"])
            self.assertFalse(report["both_image_digests_match"])
            self.assertFalse(
                report["image_evidence"]["efi"]["matches_independently_supplied_sha256"]
            )
            self.assertEqual(report["decision"], "HOLD")

    def test_denies_duplicate_missing_symlink_hardlink_and_device_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            media, root, efi = fixture(Path(temp))
            good_root = hashlib.sha256(root.read_bytes()).hexdigest()
            good_efi = hashlib.sha256(efi.read_bytes()).hexdigest()
            alias = media / "alias.img"
            alias.symlink_to(efi)
            outside = Path(temp) / "outside.img"
            outside.write_bytes(b"outside")
            hardlink = media / "hardlink.img"
            hardlink.hardlink_to(root)
            for root_path, efi_path, media_path in (
                (root, root, media),
                (alias, efi, media),
                (root, outside, media),
                (root, hardlink, media),
                (root, media / "not-created.img", media),
                (root, efi, Path("/")),
                (root, efi, Path("/dev")),
            ):
                with self.subTest(root=root_path, efi=efi_path, media=media_path):
                    report = mod.inspect(
                        media_root=media_path, root_image=root_path,
                        efi_image=efi_path,
                        expected_root_sha=good_root, expected_efi_sha=good_efi,
                    )
                    self.assertFalse(report["both_image_digests_match"])
                    self.assertFalse(report["observation_complete"])
                    self.assertEqual(report["decision"], "HOLD")

    def test_malformed_pins_and_relative_image_paths_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            media, root, efi = fixture(Path(temp))
            valid = hashlib.sha256(root.read_bytes()).hexdigest()
            for bad in ("not-a-sha", "A" * 64, "", "0" * 65):
                with self.subTest(pin=bad):
                    report = mod.inspect(
                        media_root=media, root_image=root, efi_image=efi,
                        expected_root_sha=bad, expected_efi_sha=valid,
                    )
                    self.assertFalse(report["observation_complete"])
            report = mod.inspect(
                media_root=media, root_image=Path("x1-root.img"), efi_image=efi,
                expected_root_sha=valid, expected_efi_sha=valid,
            )
            self.assertFalse(report["observation_complete"])


if __name__ == "__main__":
    unittest.main()
