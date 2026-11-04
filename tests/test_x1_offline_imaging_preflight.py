"""Synthetic, no-IO tests for the read-only offline imaging preflight."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "research/x1_offline_imaging_preflight.py"
SPEC = importlib.util.spec_from_file_location("imaging_preflight", SOURCE)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def fixtures():
    root = {
        "path": "/dev/nvme0n1p6",
        "is_block": True,
        "device_id": "259:6",
        "mounted": False,
        "size": preflight.ROOT_SIZE,
        "fstype": "ext4",
        "partuuid": "root-id",
    }
    efi = {
        "path": "/dev/nvme0n1p1",
        "is_block": True,
        "device_id": "259:1",
        "mounted": False,
        "size": preflight.EFI_SIZE,
        "fstype": "vfat",
        "partuuid": "efi-id",
    }
    target = {
        "path": "/nas/backups",
        "exists": True,
        "free_bytes": 40_000_000_000_000,
        "remote_mount_confirmed": True,
    }
    return root, efi, target


def evaluate(root, efi, target, uid=0, root_uuid="root-id", efi_uuid="efi-id"):
    return preflight.evaluate(root, efi, target, uid, root_uuid, efi_uuid)


class OfflineImagingTests(unittest.TestCase):
    def test_perfect_mock_evidence_never_authorizes_image(self):
        r, e, t = fixtures()
        result = evaluate(r, e, t)
        self.assertTrue(result["blocked"])
        self.assertFalse(result["may_execute_imaging"])
        self.assertFalse(result["may_modify_package_database"])
        self.assertIn("independent_restore_not_verified", result["hold_reasons"])

    def test_live_root_must_block(self):
        r, e, t = fixtures()
        r["mounted"] = True
        self.assertIn("root_still_mounted", evaluate(r, e, t)["hold_reasons"])

    def test_mounted_efi_must_block(self):
        r, e, t = fixtures()
        e["mounted"] = True
        self.assertIn("efi_still_mounted", evaluate(r, e, t)["hold_reasons"])

    def test_source_not_block_device(self):
        r, e, t = fixtures()
        r["is_block"] = False
        self.assertIn("root_not_block_device", evaluate(r, e, t)["hold_reasons"])

    def test_wrong_root_size(self):
        r, e, t = fixtures()
        r["size"] -= 4096
        self.assertIn("root_unexpected_size_or_filesystem", evaluate(r, e, t)["hold_reasons"])

    def test_wrong_efi_filesystem(self):
        r, e, t = fixtures()
        e["fstype"] = "ext4"
        self.assertIn("efi_unexpected_size_or_filesystem", evaluate(r, e, t)["hold_reasons"])

    def test_wrong_root_partuuid(self):
        r, e, t = fixtures()
        self.assertIn(
            "root_partuuid_not_independently_matched",
            evaluate(r, e, t, root_uuid="different")["hold_reasons"],
        )

    def test_missing_identity_authority(self):
        r, e, t = fixtures()
        result = evaluate(r, e, t, root_uuid="", efi_uuid="")
        self.assertIn("root_partuuid_not_independently_matched", result["hold_reasons"])
        self.assertIn("efi_partuuid_not_independently_matched", result["hold_reasons"])

    def test_insufficient_space(self):
        r, e, t = fixtures()
        t["free_bytes"] = preflight.MIN_TARGET_FREE - 1
        self.assertIn("target_absent_or_insufficient_capacity", evaluate(r, e, t)["hold_reasons"])

    def test_local_fake_nas_directory_blocks_even_if_plenty_of_space(self):
        r, e, t = fixtures()
        t["remote_mount_confirmed"] = False
        self.assertIn("backup_target_not_verified_network_mount", evaluate(r, e, t)["hold_reasons"])

    def test_device_aliasing_blocks_even_with_different_paths(self):
        r, e, t = fixtures()
        e["device_id"] = r["device_id"]
        self.assertIn("source_partitions_are_identical", evaluate(r, e, t)["hold_reasons"])

    def test_source_aliasing(self):
        r, e, t = fixtures()
        e["path"] = r["path"]
        self.assertIn("source_partitions_are_identical", evaluate(r, e, t)["hold_reasons"])

    def test_unprivileged(self):
        r, e, t = fixtures()
        self.assertIn(
            "not_authorized_rescue_privilege", evaluate(r, e, t, uid=1000)["hold_reasons"]
        )

    def test_autofs_plus_active_cifs_mount_accepted(self):
        import unittest.mock

        def command(*args):
            return (0, "autofs\ncifs") if args[0] == "findmnt" else (0, "Avail\n40000000000000")

        with unittest.mock.patch.object(preflight, "command", side_effect=command):
            with unittest.mock.patch.object(Path, "is_dir", return_value=True):
                result = preflight.target_info("/nas/backups")
        self.assertTrue(result["remote_mount_confirmed"])

    def test_autofs_alone_never_confirms_network_target(self):
        import unittest.mock

        def command(*args):
            return (0, "autofs") if args[0] == "findmnt" else (0, "Avail\n40000000000000")

        with unittest.mock.patch.object(preflight, "command", side_effect=command):
            with unittest.mock.patch.object(Path, "is_dir", return_value=True):
                result = preflight.target_info("/nas/backups")
        self.assertFalse(result["remote_mount_confirmed"])

    def test_missing_nas(self):
        r, e, t = fixtures()
        t["exists"] = False
        self.assertIn("target_absent_or_insufficient_capacity", evaluate(r, e, t)["hold_reasons"])


if __name__ == "__main__":
    unittest.main()
