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


def synthetic_efi_mount(proc: Path, efi: Path, *, filesystem: str = "vfat",
                        duplicate: bool = False) -> None:
    (proc / "self").mkdir(exist_ok=True)
    line = f"36 25 8:1 / {efi} rw,relatime - {filesystem} /dev/synthetic rw\n"
    (proc / "self" / "mountinfo").write_text(line * (2 if duplicate else 1))


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
        self.assertEqual(report["critical_packages"]["libc6"]["state"], "iHR")

    def test_broken_multilib_order_never_gets_hidden(self):
        for rows in (
            ("libc6:amd64\tii \t2.43-2ubuntu2\n",
             "libc6:i386\tiHR\t2.39-0ubuntu8.9\n"),
            ("libc6:i386\tiHR\t2.39-0ubuntu8.9\n",
             "libc6:amd64\tii \t2.43-2ubuntu2\n"),
        ):
            with self.subTest(rows=rows):
                report = mod.inventory_packages("".join(rows))
                self.assertEqual(report["incomplete_installed_packages"], 1)
                self.assertEqual(report["critical_packages"]["libc6"]["state"], "iHR")

    def test_duplicate_package_identity_or_bad_critical_version_denied(self):
        for data in (
            "libc6:amd64\tii \t2.43\nlibc6:amd64\tii \t2.43\n",
            "libc6:amd64\tii \tmalformed/version\n",
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                mod.inventory_packages(data)

    def test_foreign_architecture_libc_cannot_impersonate_native_installed(self):
        foreign_only = mod.inventory_packages(
            "libc6:i386\tii \t2.43-2ubuntu2\n"
        )
        self.assertEqual(foreign_only["incomplete_installed_packages"], 0)
        self.assertFalse(foreign_only["native_amd64_libc_installed"])
        mixed = mod.inventory_packages(
            "libc6:i386\tii \t2.43-2ubuntu2\n"
            "libc6:amd64\tiHR\t2.39-0ubuntu8.9\n"
        )
        self.assertFalse(mixed["native_amd64_libc_installed"])
        self.assertEqual(mixed["incomplete_installed_packages"], 1)
        native = mod.inventory_packages(
            "libc6:amd64\tii \t2.43-2ubuntu2\n"
        )
        self.assertTrue(native["native_amd64_libc_installed"])

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
            efi = p / "efi"
            efi.mkdir()
            synthetic_efi_mount(p, efi)
            report = mod.collect(run=run, proc=p, etc=p, root=p, efi=efi)
            self.assertTrue(report["observations_complete"])
            self.assertEqual(report["package_state"], "NO_DPKG_INCOMPLETE_INSTALLED_DETECTED")
            self.assertFalse(report["production_change_permitted"])
            missing_efi = mod.collect(
                run=run, proc=p, etc=p, root=p, efi=p / "not-present"
            )
            self.assertFalse(missing_efi["observations_complete"])
            self.assertEqual(missing_efi["failure"], "READ_ONLY_EVIDENCE_INCOMPLETE")
            self.assertEqual(missing_efi["decision"], "HOLD")
            self.assertFalse(report["package_repair_permitted"])
            self.assertFalse(report["production_change_permitted"])
            self.assertEqual(report["decision"], "HOLD")
            self.assertEqual([c[0] for c in commands], ["dpkg-query", "mokutil"] * 2)

    def test_residual_config_libc_is_not_accepted_as_installed(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "os-release").write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
            (p / "meminfo").write_text(
                "MemAvailable: 4000 kB\nSwapTotal: 8192 kB\nSwapFree: 8 kB\n"
            )
            def run(argv, **_kwargs):
                if argv[0] == "dpkg-query":
                    return subprocess.CompletedProcess(
                        argv, 0, "libc6:amd64\trc \t2.39-0ubuntu8.9\n", ""
                    )
                return subprocess.CompletedProcess(argv, 0, "SecureBoot enabled\n", "")
            efi = p / "efi"
            efi.mkdir()
            synthetic_efi_mount(p, efi)
            report = mod.collect(run=run, proc=p, etc=p, root=p, efi=efi)
            self.assertTrue(report["observations_complete"])
            self.assertEqual(report["package_state"], "INCOMPLETE")
            self.assertEqual(report["decision"], "HOLD")

    def test_missing_meminfo_or_unverified_secure_boot_is_not_clean_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "os-release").write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
            (p / "meminfo").write_text("MemAvailable: 4000 kB\n")
            def run(argv, **_kwargs):
                if argv[0] == "dpkg-query":
                    return subprocess.CompletedProcess(
                        argv, 0, "libc6:amd64\tii \t2.43-2ubuntu2\n", ""
                    )
                return subprocess.CompletedProcess(argv, 0, "unexpected SecureBoot text\n", "")
            efi = p / "efi"
            efi.mkdir()
            synthetic_efi_mount(p, efi)
            report = mod.collect(run=run, proc=p, etc=p, root=p, efi=efi)
            self.assertEqual(report["package_state"], "NO_DPKG_INCOMPLETE_INSTALLED_DETECTED")
            self.assertFalse(report["observations_complete"])
            self.assertEqual(report["secure_boot"], "UNVERIFIED")
            self.assertEqual(report["decision"], "HOLD")

    def test_efi_directory_without_real_vfat_mount_is_not_clean(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            efi = p / "efi"
            efi.mkdir()
            (p / "os-release").write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
            (p / "meminfo").write_text(
                "MemAvailable: 4000 kB\nSwapTotal: 8192 kB\nSwapFree: 8 kB\n"
            )
            def run(argv, **_kwargs):
                if argv[0] == "dpkg-query":
                    return subprocess.CompletedProcess(
                        argv, 0, "libc6:amd64\tii \t2.43-2ubuntu2\n", ""
                    )
                return subprocess.CompletedProcess(argv, 0, "SecureBoot enabled\n", "")
            for mode in ("not-mounted", "ext4", "duplicate"):
                with self.subTest(mode=mode):
                    if mode == "not-mounted":
                        (p / "self").mkdir(exist_ok=True)
                        (p / "self" / "mountinfo").write_text(
                            "36 25 8:1 / /unrelated rw - vfat /dev/synthetic rw\n"
                        )
                    else:
                        synthetic_efi_mount(p, efi, filesystem="ext4" if mode == "ext4" else "vfat",
                                            duplicate=mode == "duplicate")
                    report = mod.collect(run=run, proc=p, etc=p, root=p, efi=efi)
                    self.assertEqual(report["package_state"],
                                     "NO_DPKG_INCOMPLETE_INSTALLED_DETECTED")
                    self.assertFalse(report["efi_mounted_vfat"])
                    self.assertFalse(report["observations_complete"])
                    self.assertEqual(report["decision"], "HOLD")


    def test_tool_failure_is_not_false_green(self):
        def fail(*a, **kw):
            raise subprocess.TimeoutExpired("dpkg-query", 12)
        report = mod.collect(run=fail)
        self.assertEqual(report["package_state"], "UNVERIFIED")
        self.assertFalse(report["observations_complete"])
        self.assertFalse(report["reboot_permitted"])


if __name__ == "__main__":
    unittest.main()
