"""Synthetic primary/backup GPT evidence gate; never authorizes x1 repair."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zlib

PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_x1_offline_gpt_mirror.py"
spec = importlib.util.spec_from_file_location("x1_offline_mirror", PATH)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SECTORS = 2048
TABLE_BYTES = 128 * 128
LAST_LBA = SECTORS - 1
BACK_TABLE_LBA = LAST_LBA - TABLE_BYTES // 512


def disk_fixture():
    data = bytearray(SECTORS * 512)
    data[450] = 0xEE
    data[510:512] = b"\x55\xaa"
    entries = bytearray(TABLE_BYTES)
    entries[:16] = bytes(range(1, 17))
    entries[16:32] = bytes(range(17, 33))
    array_crc = zlib.crc32(entries) & 0xFFFFFFFF
    data[1024:1024 + TABLE_BYTES] = entries
    data[BACK_TABLE_LBA * 512:LAST_LBA * 512] = entries

    def header(*, current, backup, table_lba):
        h = bytearray(512)
        h[:8] = b"EFI PART"
        h[8:12] = (0x00010000).to_bytes(4, "little")
        h[12:16] = (92).to_bytes(4, "little")
        h[24:32] = current.to_bytes(8, "little")
        h[32:40] = backup.to_bytes(8, "little")
        h[40:48] = (34).to_bytes(8, "little")
        h[48:56] = (BACK_TABLE_LBA - 1).to_bytes(8, "little")
        h[56:72] = b"\x44" * 16
        h[72:80] = table_lba.to_bytes(8, "little")
        h[80:84] = (128).to_bytes(4, "little")
        h[84:88] = (128).to_bytes(4, "little")
        h[88:92] = array_crc.to_bytes(4, "little")
        h[16:20] = (zlib.crc32(h[:92]) & 0xFFFFFFFF).to_bytes(4, "little")
        return h

    data[512:1024] = header(current=1, backup=LAST_LBA, table_lba=2)
    data[LAST_LBA * 512:] = header(
        current=LAST_LBA, backup=1, table_lba=BACK_TABLE_LBA,
    )
    return data


class MirrorTests(unittest.TestCase):
    def test_realistic_synthetic_disk_mirror_matching_is_still_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "offhost-copy"
            media.mkdir()
            disk = media / "raw.img"
            disk.write_bytes(disk_fixture())
            proof = mod.inspect(media, disk)
            self.assertTrue(proof["observation_complete"])
            self.assertTrue(proof["primary_backup_gpt_and_array_match"])
            self.assertFalse(proof["clone_boot_verified"])
            self.assertFalse(proof["signed_backup_custody_verified"])
            self.assertFalse(proof["production_host_repair_allowed"])
            self.assertEqual(proof["decision"], "HOLD")
            self.assertNotIn(str(media), json.dumps(proof))
            self.assertEqual(mod.main([
                "--explicit-offhost-readonly", "--media-root", str(media),
                "--disk-image", str(disk),
            ]), 2)

    def test_tampered_primary_backup_or_mirror_table_fail(self):
        for offset in (
            450, 510, 512, 528, 535, 600, 1024, 1033,
            BACK_TABLE_LBA * 512,
            BACK_TABLE_LBA * 512 + 25,
            LAST_LBA * 512 + 12,
            LAST_LBA * 512 + 16,
            LAST_LBA * 512 + 24,
            LAST_LBA * 512 + 56,
            LAST_LBA * 512 + 88,
        ):
            with self.subTest(offset=offset):
                with tempfile.TemporaryDirectory() as tmp:
                    media = Path(tmp) / "offhost-copy"
                    media.mkdir()
                    disk = media / "raw.img"
                    data = disk_fixture()
                    data[offset] ^= 0xFF
                    disk.write_bytes(data)
                    proof = mod.inspect(media, disk)
                    self.assertFalse(
                        proof["primary_backup_gpt_and_array_match"],
                        f"forged mirror accidentally accepted offset {offset}",
                    )
                    self.assertFalse(proof["production_host_repair_allowed"])

    def test_invalid_media_root_and_symlinked_disk_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            media = base / "offhost-copy"
            media.mkdir()
            disk = media / "raw.img"
            disk.write_bytes(disk_fixture())
            link = media / "linked.img"
            link.symlink_to(disk)
            for path, root in (
                (link, media),
                (disk, Path("/dev")),
                (disk, Path("/")),
            ):
                with self.subTest(path=path, root=root):
                    proof = mod.inspect(root, path)
                    self.assertFalse(proof["observation_complete"])
                    self.assertEqual(proof["decision"], "HOLD")

    def test_truncated_disk_and_missing_secondary_gpt_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "offhost-copy"
            media.mkdir()
            disk = media / "raw.img"
            data = disk_fixture()
            for raw in (
                data[:1024],
                data[:-512],
                data[:-1],
                data[:LAST_LBA * 512] + bytearray(512),
            ):
                disk.write_bytes(raw)
                result = mod.inspect(media, disk)
                self.assertFalse(result["primary_backup_gpt_and_array_match"])


if __name__ == "__main__":
    unittest.main()
