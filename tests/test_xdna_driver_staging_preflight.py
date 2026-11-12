"""Synthetic offline tests for the never-authorizing staging preflight."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "research/xdna_driver_staging_preflight.py"
SPEC = importlib.util.spec_from_file_location("xdna_driver_gate", SRC)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def fixture() -> dict:
    return {
        "hostname": "test-node",
        "critical_os_packages_configured": True,
        "critical_os_problem_packages": [],
        "kernel": "7.0.0-42-generic",
        "module_readable": True,
        "active_module": (
            "/lib/modules/7.0.0-42-generic/kernel/drivers/accel/amdxdna/amdxdna.ko.zst"
        ),
        "module_signer_present": True,
        "secure_boot_known": True,
        "secure_boot_enabled": True,
        "vendor_source_sha256": gate.VENDOR_GEM_SHA256,
        "vendor_mapping_fix_count": 2,
        "dkms_command_installed": True,
        "module_sign_file_available": True,
        "candidate_signed_module_verified": False,
        "mok_enrollment_metadata_readable": True,
        "mok_enrollment_subject_count": 1,
        "mok_noncanonical_subject_count": 0,
        "mok_trust_verified_for_candidate": False,
        "rollback_boot_proven": False,
        "alternate_kernel_images": ["vmlinuz-7.0.0-38-generic", "vmlinuz-7.0.0-42-generic"],
        "npu_device_present": True,
        "npu_idle_confirmed": True,
        "available_memory_kib": 35_000_000,
        "load_average_1m": 0.5,
        "disk_free_bytes": 100 * 1024**3,
    }


class DriverReadinessTests(unittest.TestCase):
    def test_best_case_still_blocks_unsigned_unapproved_candidate(self):
        report = gate.evaluate(fixture())
        self.assertTrue(report["blocked"])
        self.assertFalse(report["production_route_eligible"])
        self.assertFalse(report["driver_replacement_authorized"])
        self.assertIn("candidate_signature_unverified", report["hold_reasons"])

    def test_unverified_source_blocks(self):
        s = fixture()
        s["vendor_source_sha256"] = "bad"
        self.assertIn("vendor_source_identity_unverified", gate.evaluate(s)["hold_reasons"])

    def test_half_installed_glibc_blocks_driver_staging(self):
        s = fixture()
        s["critical_os_packages_configured"] = False
        s["critical_os_problem_packages"] = ["libc6"]
        report = gate.evaluate(s)
        self.assertIn("critical_os_package_state_incomplete", report["hold_reasons"])
        self.assertEqual(report["critical_os_problem_packages"], ["libc6"])
        self.assertFalse(report["driver_replacement_authorized"])

    def test_unconfigured_grub_blocks_driver_staging(self):
        s = fixture()
        s["critical_os_packages_configured"] = False
        s["critical_os_problem_packages"] = ["grub-efi-amd64-signed"]
        self.assertIn("critical_os_package_state_incomplete", gate.evaluate(s)["hold_reasons"])

    def test_incomplete_package_parser_is_fail_closed(self):
        import unittest.mock

        fake_output = (
            "libc6\tiHR\nlibc-bin\tii \nlocales\tiU\n"
            "grub-efi-amd64-signed\tiU\nshim-signed\tiU\n"
            "libamdhip64-7\tiU"
        )
        with unittest.mock.patch.object(gate, "run_command", return_value=(0, fake_output)):
            ok, bad = gate.critical_os_package_state()
        self.assertFalse(ok)
        self.assertIn("libc6", bad)
        self.assertIn("shim-signed", bad)

    def test_complete_package_parser_passes_only_exact_ii(self):
        import unittest.mock

        raw = "".join(f"{name}\tii \n" for name in gate.CRITICAL_OS_PACKAGES)
        with unittest.mock.patch.object(gate, "run_command", return_value=(0, raw)):
            ok, bad = gate.critical_os_package_state()
        self.assertTrue(ok)
        self.assertEqual(bad, [])

    def test_missing_device_must_block_even_if_fuser_claims_idle(self):
        s = fixture()
        s["npu_device_present"] = False
        s["npu_idle_confirmed"] = True
        self.assertIn("npu_device_missing_or_not_character", gate.evaluate(s)["hold_reasons"])

    def test_missing_mok_inventory_blocks(self):
        s = fixture()
        s["mok_enrollment_metadata_readable"] = False
        self.assertIn("mok_enrollment_inventory_unavailable", gate.evaluate(s)["hold_reasons"])

    def test_mok_subjects_do_not_equal_candidate_trust(self):
        info = gate.enrolled_mok_summary(
            "[key 1]\n Subject: C=GB, O=Canonical Ltd., CN=Canonical Ltd.\n"
        )
        self.assertEqual(info["observed_enrolled_subject_count"], 1)
        self.assertEqual(info["observed_noncanonical_subject_count"], 0)
        self.assertFalse(info["candidate_trust_proven"])

    def test_mok_custom_subjects_still_not_candidate_trust(self):
        info = gate.enrolled_mok_summary("[key 1]\n Subject: CN=Other Signing CA\n")
        self.assertEqual(info["observed_noncanonical_subject_count"], 1)
        self.assertFalse(info["candidate_trust_proven"])

    def test_missing_dkms_blocks(self):
        s = fixture()
        s["dkms_command_installed"] = False
        self.assertIn("dkms_tool_unavailable", gate.evaluate(s)["hold_reasons"])

    def test_busy_npu_blocks(self):
        s = fixture()
        s["npu_idle_confirmed"] = False
        self.assertIn("npu_not_verified_idle", gate.evaluate(s)["hold_reasons"])

    def test_unknown_secure_boot_blocks(self):
        s = fixture()
        s["secure_boot_known"] = False
        self.assertIn("secure_boot_state_unknown", gate.evaluate(s)["hold_reasons"])

    def test_missing_alternative_boot_image_blocks(self):
        s = fixture()
        s["alternate_kernel_images"] = ["vmlinuz-7.0.0-42-generic"]
        self.assertIn("alternate_kernel_image_missing", gate.evaluate(s)["hold_reasons"])

    def test_no_rollback_proof_blocks(self):
        self.assertIn("rollback_boot_unproven", gate.evaluate(fixture())["hold_reasons"])

    def test_high_load_blocks(self):
        s = fixture()
        s["load_average_1m"] = 12
        self.assertIn("host_load_outside_research_gate", gate.evaluate(s)["hold_reasons"])

    def test_default_cannot_authorize_replacement(self):
        result = gate.evaluate(fixture())
        self.assertFalse(result["isolated_reboot_authorized"])
        self.assertFalse(result["signed_candidate_runtime_tested"])

    def test_wrong_module_blocks(self):
        s = fixture()
        s["active_module"] = "/random/notknown/amdxdna.ko"
        self.assertIn("baseline_in_tree_module_changed", gate.evaluate(s)["hold_reasons"])


if __name__ == "__main__":
    unittest.main()
