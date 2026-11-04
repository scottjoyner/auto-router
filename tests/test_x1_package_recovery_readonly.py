"""Fixture-only tests: never invoke live package managers or host maintenance."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts" / "x1_package_recovery_readonly.py"
spec = importlib.util.spec_from_file_location("x1_package_recovery_readonly", PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


class AuditTests(unittest.TestCase):
    def test_half_installed_libc_and_unpacked_locale_hold(self):
        data = ("libc6:amd64\tiHR\t2.39-0ubuntu8.9\n"
                "libc-bin\tii \t2.39-0ubuntu8.9\n"
                "locales\tiU \t2.43-2ubuntu2.4\n"
                "old-config\trc \t1.0\n")
        result = mod.inventory_packages(data)
        self.assertEqual(result["incomplete_installed_packages"], 2)
        self.assertEqual(result["reinstallation_required_packages"], 1)
        self.assertEqual(result["critical_packages"]["libc6"]["state"], "iHR")

    def test_fails_closed_missing_libc_or_malformed(self):
        for data in ("", "other\tii \t1.0\n", "libc6\tinvalid\t1.0\n"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                mod.inventory_packages(data)

    def test_every_incomplete_dpkg_transition_is_a_hold(self):
        # Includes interrupted install, configure, trigger, removal and
        # error-required states; no live package manager is invoked.
        incomplete = ("iHR", "iU ", "iF ", "iW ", "it ", "ri ",
                      "pi ", "in ", "iiR", "hiR")
        for state in incomplete:
            with self.subTest(state=state):
                report = mod.inventory_packages(
                    "libc6:amd64\t" + state + "\t2.43-2ubuntu2\n"
                )
                self.assertEqual(report["incomplete_installed_packages"], 1)

    def test_unknown_dpkg_state_never_becomes_clean(self):
        for state in ("???", "ix ", "xi ", "iiX", "iI ", "iU?"):
            with self.subTest(state=state), self.assertRaises(ValueError):
                mod.inventory_packages(
                    "libc6:amd64\t" + state + "\t2.43-2ubuntu2\n"
                )

    def test_clean_hold_and_residual_config_are_not_false_pending(self):
        report = mod.inventory_packages(
            "libc6:amd64\thi \t2.43-2ubuntu2\n"
            "old-config\trc \t1.0\n"
            "removed-package\tun \t1.0\n"
        )
        self.assertEqual(report["incomplete_installed_packages"], 0)

    def test_mixed_architectures_cannot_hide_a_broken_libc(self):
        report = mod.inventory_packages(
            "libc6:i386\tiHR\t2.39-0ubuntu8.9\n"
            "libc6:amd64\tii \t2.43-2ubuntu2\n"
        )
        self.assertEqual(report["incomplete_installed_packages"], 1)
        self.assertEqual(report["reinstallation_required_packages"], 1)

    def test_clean_inventory_still_never_authorizes_repair_or_reboot(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "os-release").write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
            (p / "meminfo").write_text('MemAvailable:  400000 kB\nSwapTotal:  8192 kB\nSwapFree:  8 kB\n')
            commands = []
            def run(argv, **kwargs):
                commands.append(tuple(argv))
                if argv[0] == "dpkg-query":
                    return subprocess.CompletedProcess(argv, 0, "libc6:amd64\tii \t2.43-2ubuntu2\n", "")
                if argv[0] == "mokutil":
                    return subprocess.CompletedProcess(argv, 0, "SecureBoot enabled\n", "")
                raise AssertionError("Unexpected command")
            report = mod.collect(run=run, proc=p, etc=p, root=p, efi=p / "not-present")
            self.assertTrue(report["observations_complete"])
            self.assertEqual(report["package_state"], "NO_DPKG_INCOMPLETE_INSTALLED_DETECTED")
            self.assertFalse(report["package_repair_permitted"])
            self.assertFalse(report["production_change_permitted"])
            self.assertEqual(report["decision"], "HOLD")
            self.assertEqual([c[0] for c in commands], ["dpkg-query", "mokutil"])

    def test_tool_failure_is_not_false_green(self):
        def fail(*a, **kw):
            raise subprocess.TimeoutExpired("dpkg-query", 12)
        report = mod.collect(run=fail)
        self.assertEqual(report["package_state"], "UNVERIFIED")
        self.assertFalse(report["observations_complete"])
        self.assertFalse(report["reboot_permitted"])


if __name__ == "__main__":
    unittest.main()
