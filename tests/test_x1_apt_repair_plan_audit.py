"""Synthetic tests; no package-manager operation or disk mutation is performed."""

from __future__ import annotations

import importlib.util
import unittest
from copy import deepcopy
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "research/x1_apt_repair_plan_audit.py"
SPEC = importlib.util.spec_from_file_location("x1_apt_plan_gate", SOURCE)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def simulated_plan():
    items = {
        name: "2.43-2ubuntu2.4" if name.startswith("libc") else "other"
        for name in gate.EXPECTED_INSTALL_NAMES
    }
    return {
        "summary": {"upgraded": 8, "new": 1, "removed": 0},
        "changes": {
            "install": items,
            "configure": {name: "version" for name in gate.CRITICAL_CONFIGURE},
            "remove": {},
        },
        "errors": [],
    }


class PackageRepairPlanTests(unittest.TestCase):
    def test_mock_plan_stays_blocked_for_real_repair(self):
        result = gate.evaluate(simulated_plan(), gate.EXPECTED_STATUS_SHA, 0)
        self.assertIn("unreviewed_full_transaction_drift", result["unexpected_or_unverified_facts"])
        self.assertTrue(result["blocked"])
        self.assertFalse(result["may_execute_repair"])
        self.assertFalse(result["may_restart_services"])
        self.assertFalse(result["production_npu_route_eligible"])

    def test_exact_approved_synthetic_plan_hash_can_match(self):
        synthetic = simulated_plan()
        first = gate.evaluate(synthetic, gate.EXPECTED_STATUS_SHA, 0)
        matched = gate.evaluate(
            synthetic,
            gate.EXPECTED_STATUS_SHA,
            0,
            expected_plan_sha=first["plan_sha256"],
        )
        self.assertTrue(matched["baseline_matches"])
        self.assertTrue(matched["blocked"])
        self.assertFalse(matched["may_execute_repair"])

    def test_extra_configure_step_fails_pinned_transaction(self):
        base = simulated_plan()
        reference = gate.evaluate(base, gate.EXPECTED_STATUS_SHA, 0)["plan_sha256"]
        changed = deepcopy(base)
        changed["changes"]["configure"]["some-new-package"] = "v"
        report = gate.evaluate(
            changed,
            gate.EXPECTED_STATUS_SHA,
            0,
            expected_plan_sha=reference,
        )
        self.assertIn("unreviewed_full_transaction_drift", report["unexpected_or_unverified_facts"])

    def test_status_drift_holds(self):
        report = gate.evaluate(simulated_plan(), "changed", 0)
        self.assertIn("dpkg_status_drift", report["unexpected_or_unverified_facts"])

    def test_apt_failure_holds(self):
        report = gate.evaluate(simulated_plan(), gate.EXPECTED_STATUS_SHA, 100)
        self.assertIn("apt_simulation_failed", report["unexpected_or_unverified_facts"])

    def test_timeout_holds(self):
        report = gate.evaluate(simulated_plan(), gate.EXPECTED_STATUS_SHA, 127, True)
        self.assertIn("apt_simulation_failed", report["unexpected_or_unverified_facts"])

    def test_unexpected_remove_holds(self):
        plan = deepcopy(simulated_plan())
        plan["changes"]["remove"]["tailscaled"] = "old"
        report = gate.evaluate(plan, gate.EXPECTED_STATUS_SHA, 0)
        self.assertIn("unexpected_package_removal", report["unexpected_or_unverified_facts"])

    def test_changed_transaction_counts_hold(self):
        plan = deepcopy(simulated_plan())
        plan["summary"]["new"] = 2
        report = gate.evaluate(plan, gate.EXPECTED_STATUS_SHA, 0)
        self.assertIn("unexpected_transaction_counts", report["unexpected_or_unverified_facts"])

    def test_unexpected_install_set_holds(self):
        plan = deepcopy(simulated_plan())
        plan["changes"]["install"]["docker-ce"] = "latest"
        report = gate.evaluate(plan, gate.EXPECTED_STATUS_SHA, 0)
        self.assertIn("unexpected_install_set", report["unexpected_or_unverified_facts"])

    def test_wrong_libc_target_holds(self):
        plan = deepcopy(simulated_plan())
        plan["changes"]["install"]["libc6"] = "99.0"
        report = gate.evaluate(plan, gate.EXPECTED_STATUS_SHA, 0)
        self.assertIn("unexpected_glibc_target_version", report["unexpected_or_unverified_facts"])

    def test_missing_grub_configure_step_holds(self):
        plan = deepcopy(simulated_plan())
        del plan["changes"]["configure"]["grub-efi-amd64-signed"]
        report = gate.evaluate(plan, gate.EXPECTED_STATUS_SHA, 0)
        self.assertIn(
            "critical_package_configure_steps_missing", report["unexpected_or_unverified_facts"]
        )

    def test_error_line_detected(self):
        plan = gate.parse_simulation("E: Unmet dependencies\n")
        self.assertEqual(plan["errors"], ["apt_simulation_error_present"])

    def test_inst_conf_remv_parser(self):
        plan = gate.parse_simulation(
            "8 upgraded, 1 newly installed, 0 to remove and 1725 not upgraded.\n"
            "Inst libc6 [2.39] (2.43-2ubuntu2.4 Ubuntu:26.04 [amd64])\n"
            "Conf libc6 (2.43-2ubuntu2.4 Ubuntu:26.04 [amd64])\n"
            "Remv old-package [1.0]\n"
        )
        self.assertEqual(plan["changes"]["install"]["libc6"], "2.43-2ubuntu2.4")
        self.assertEqual(plan["changes"]["configure"]["libc6"], "2.43-2ubuntu2.4")
        self.assertIn("old-package", plan["changes"]["remove"])

    def test_changed_summary_hash_is_detectable(self):
        base = gate.evaluate(simulated_plan(), gate.EXPECTED_STATUS_SHA, 0)
        changed = deepcopy(simulated_plan())
        changed["changes"]["configure"]["new-package"] = "v"
        new = gate.evaluate(changed, gate.EXPECTED_STATUS_SHA, 0)
        self.assertNotEqual(base["plan_sha256"], new["plan_sha256"])


if __name__ == "__main__":
    unittest.main()
