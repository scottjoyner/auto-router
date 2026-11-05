"""Synthetic, bounded offline root+EFI header inspection: no disk/mount access."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zlib

PATH = Path(__file__).resolve().parents[1] / "scripts" / "inspect_x1_offline_boot_structure.py"
spec = importlib.util.spec_from_file_location("x1_boot_headers", PATH)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fat32_header():
    data = bytearray(512)
    data[11:13] = (512).to_bytes(2, "little")
    data[13] = 8
    data[14:16] = (32).to_bytes(2, "little")
    data[82:90] = b"FAT32   "
    data[510:512] = b"\x55\xaa"
    return bytes(data)


def valid_primary_gpt():
    data = bytearray(1024)
    data[450] = 0xEE  # Protective MBR partition type.
    data[510:512] = b"\\x55\\xaa"
    header = bytearray(512)
    header[:8] = b"EFI PART"
    header[8:12] = (0x00010000).to_bytes(4, "little")
    header[12:16] = (92).to_bytes(4, "little")
    header[24:32] = (1).to_bytes(8, "little")
    header[32:40] = (1000).to_bytes(8, "little")
    header[40:48] = (34).to_bytes(8, "little")
    header[48:56] = (967).to_bytes(8, "little")
    header[56:72] = b"\\x11" * 16
    header[72:80] = (2).to_bytes(8, "little")
    header[80:84] = (128).to_bytes(4, "little")
    header[84:88] = (128).to_bytes(4, "little")
    header[16:20] = (zlib.crc32(header[:92]) & 0xFFFFFFFF).to_bytes(4, "little")
    data[512:1024] = header
    return bytes(data)


class BootStructureInspectionTests(unittest.TestCase):
    def test_offline_ext_and_efi_headers_are_observations_not_boot_certs(self):
        with tempfile.TemporaryDirectory() as temp:
            media = Path(temp) / "offline-copy"
            media.mkdir()
            root = media / "root.img"
            efi = media / "efi.img"
            root_bytes = bytearray(4096)
            root_bytes[1080:1082] = b"\x53\xef"
            root.write_bytes(root_bytes)
            efi.write_bytes(fat32_header())
            report = mod.inspect(media, root, efi)
            self.assertTrue(report["observation_complete"])
            self.assertEqual(report["root_header_class"], "ext-filesystem")
            self.assertEqual(report["efi_header_class"], "fat32-partition-header")
            self.assertFalse(report["boot_rehearsal_performed"])
            self.assertFalse(report["production_change_permitted"])
            self.assertEqual(report["decision"], "HOLD")
            self.assertNotIn(str(media), json.dumps(report))
            self.assertEqual(mod.main([
                "--explicit-off-host-read-only",
                "--media-root", str(media),
                "--root-image", str(root),
                "--efi-image", str(efi),
            ]), 2)

    def test_recognizes_known_raw_headers_and_does_not_claim_boot(self):
        gpt = valid_primary_gpt()
        btrfs = bytearray(0x10048)
        btrfs[0x10040:0x10048] = b"_BHRfS_M"
        cases = [
            (b"LUKS\xba\xbe" + b"\x00" * 100, "luks-encrypted-volume"),
            (b"XFSB" + b"\x00" * 100, "xfs-filesystem"),
            (bytes(gpt), "raw-gpt-disk-header"),
            (bytes(btrfs), "btrfs-filesystem"),
            (b"\x00" * 100, "unknown"),
        ]
        for data, kind in cases:
            with self.subTest(kind=kind):
                self.assertEqual(mod.root_kind(data), kind)
        self.assertEqual(mod.efi_kind(bytes(gpt)), "raw-gpt-disk-header")
        self.assertEqual(mod.efi_kind(b"not an EFI partition"), "unknown")

    def test_corrupt_primary_gpt_magic_crc_geometry_or_protective_mbr_denied(self):
        clean = valid_primary_gpt()
        self.assertTrue(mod._valid_primary_gpt_header(clean))
        for offset in (
            512,  # signature, even with intact CRC
            528,  # header CRC32
            536,  # current LBA (invalid checksum)
            450,  # protective MBR
            510,  # 0x55AA MBR signature
        ):
            mutated = bytearray(clean)
            mutated[offset] ^= 0xFF
            with self.subTest(offset=offset):
                self.assertFalse(mod._valid_primary_gpt_header(mutated))
                self.assertEqual(mod.root_kind(mutated), "unknown")
                self.assertEqual(mod.efi_kind(mutated), "unknown")
        magic_only = bytearray(1024)
        magic_only[512:520] = b"EFI PART"
        self.assertEqual(mod.root_kind(magic_only), "unknown")

    def test_corrupt_fat_header_is_not_recognized(self):
        baseline = bytearray(fat32_header())
        for i, corrupt in (
            (11, 0), (13, 3), (14, 0), (510, 0),
        ):
            altered = bytearray(baseline)
            altered[i] = corrupt
            if i == 11:
                altered[12] = 0
            if i == 14:
                altered[15] = 0
            self.assertEqual(mod.efi_kind(altered), "unknown")

    def test_refuses_symlink_parent_and_leaf_and_non_regular_files(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            media = base / "offline-copy"
            media.mkdir()
            root = media / "root.img"
            efi = media / "efi.img"
            root.write_bytes(b"XFSB" + b"\0" * 4096)
            efi.write_bytes(fat32_header())
            inner = media / "inner"
            inner.mkdir()
            redirect = media / "redirect"
            redirect.symlink_to(inner, target_is_directory=True)
            link = media / "root-link"
            link.symlink_to(root)
            for image in (redirect / "root.img", link, inner):
                with self.subTest(image=image):
                    report = mod.inspect(media, image, efi)
                    self.assertFalse(report["observation_complete"])
                    self.assertFalse(report["production_change_permitted"])
            self.assertFalse(mod.inspect(media, root, root)["observation_complete"])
            self.assertFalse(mod.inspect(Path("/dev"), root, efi)["observation_complete"])

    def test_unknown_headers_do_not_authorize_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            media = Path(temp) / "offline-copy"
            media.mkdir()
            root = media / "root.img"
            efi = media / "efi.img"
            root.write_bytes(b"unknown content")
            efi.write_bytes(b"unknown")
            report = mod.inspect(media, root, efi)
            self.assertTrue(report["observation_complete"])
            self.assertFalse(report["recognized_root_header"])
            self.assertFalse(report["recognized_efi_header"])
            self.assertEqual(report["decision"], "HOLD")


if __name__ == "__main__":
    unittest.main()
