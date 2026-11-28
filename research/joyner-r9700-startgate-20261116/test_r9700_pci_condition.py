"""Synthetic and side-effect-free PCI-condition contract tests."""
import contextlib
from configparser import ConfigParser
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import r9700_pci_condition as guard


def make_dev(root, address, vendor="0x1002", device="0x7551"):
    p = root / address
    p.mkdir()
    (p / "vendor").write_text(vendor)
    (p / "device").write_text(device)
    return p


class PciConditionTests(unittest.TestCase):
    def test_missing_root_refuses(self):
        with TemporaryDirectory() as t:
            self.assertEqual(guard.assess(Path(t) / "absent"),
                             "HOLD_PCI_EVIDENCE_UNAVAILABLE")

    def test_empty_or_only_igpu_refuses(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            self.assertEqual(guard.assess(root), "HOLD_PCI_R9700_ABSENT")
            make_dev(root, "0000:01:00.0", "0x1002", "0x164e")
            self.assertEqual(guard.assess(root), "HOLD_PCI_R9700_ABSENT")

    def test_exact_one_device_is_only_pci_condition(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            make_dev(root, "0000:01:00.0", "0x1002", "0x164e")
            make_dev(root, "0000:02:00.0")
            self.assertEqual(guard.assess(root), "PCI_DEVICE_ENUMERATED_ONLY")

    def test_duplicate_target_fails_closed(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            make_dev(root, "0000:02:00.0")
            make_dev(root, "0000:03:00.0")
            self.assertEqual(guard.assess(root), "HOLD_MULTIPLE_R9700_FUNCTIONS")

    def test_missing_malformed_or_unexpected_entries_fail(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            d = make_dev(root, "0000:01:00.0")
            (d / "vendor").unlink()
            self.assertEqual(guard.assess(root), "HOLD_PCI_EVIDENCE_UNAVAILABLE")
        with TemporaryDirectory() as t:
            root = Path(t)
            make_dev(root, "0000:01:00.0", vendor="0xnosuch")
            self.assertEqual(guard.assess(root), "HOLD_MALFORMED_PCI_ID")
        with TemporaryDirectory() as t:
            root = Path(t)
            make_dev(root, "not-a-pci-address")
            self.assertEqual(guard.assess(root), "HOLD_UNEXPECTED_PCI_ENTRY")

    def test_bounded_scan_rejects_oversized_or_invalid_limit(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            make_dev(root, "0000:01:00.0")
            make_dev(root, "0000:02:00.0")
            self.assertEqual(guard.assess(root, limit=1),
                             "HOLD_PCI_ENUMERATION_TOO_LARGE")
            for limit in (0, -1, 513, True, "2"):
                with self.subTest(limit=limit):
                    self.assertEqual(guard.assess(root, limit=limit),
                                     "HOLD_INVALID_LIMIT")

    def test_systemd_skip_and_yes_are_not_readiness(self):
        for code, expected in (("HOLD_PCI_R9700_ABSENT", 1),
                               ("PCI_DEVICE_ENUMERATED_ONLY", 0),
                               ("HOLD_PCI_EVIDENCE_UNAVAILABLE", 1)):
            with self.subTest(code=code), patch.object(guard, "assess", return_value=code):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = guard.main()
                receipt = json.loads(buf.getvalue())
                self.assertEqual(rc, expected)
                self.assertFalse(receipt["driverReady"])
                self.assertFalse(receipt["inferenceReady"])
                self.assertFalse(receipt["serviceChanged"])
                self.assertEqual(receipt["hardwareConditionSatisfied"], expected == 0)

    def test_proposed_dropins_bound_retries_without_starting_units(self):
        path = Path(__file__).with_name("proposed")
        for unit in ("lfm3-joyner-inference", "kipnerter-lfm3-r9700-bridge"):
            cfg = ConfigParser(interpolation=None)
            cfg.read(path / (unit + ".service.d-guard.conf"))
            self.assertEqual(cfg["Unit"]["StartLimitIntervalSec"], "600")
            self.assertEqual(cfg["Unit"]["StartLimitBurst"], "3")
            self.assertEqual(cfg["Service"]["ExecCondition"],
                             "/usr/bin/python3 -B /opt/fleet/bin/r9700_pci_condition.py")
            self.assertNotIn("ExecStart", cfg["Service"])
            self.assertNotIn("Restart", cfg["Service"])


if __name__ == "__main__":
    unittest.main()
