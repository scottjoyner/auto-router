"""Synthetic tests for Fleet Commander service/authority separation."""

from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / "research/x1_control_plane_boundary_audit.py"
SPEC = importlib.util.spec_from_file_location("x1_control_audit", SOURCE)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def all_healthy():
    return {
        unit: {
            "ActiveState": "active",
            "SubState": "running",
            "NoNewPrivileges": "yes",
            "NRestarts": "0",
            "Result": "success",
        }
        for unit in audit.UNITS
    }


class ControlBoundaryTests(unittest.TestCase):
    def test_all_services_pass_never_authorizes_privileged_repair(self):
        report = audit.evaluate(all_healthy(), True)
        self.assertTrue(report["mcp_and_fleet_exec_service_checks_pass"])
        self.assertFalse(report["privileged_recovery_authorized"])
        self.assertFalse(report["may_run_sudo"])
        self.assertFalse(report["may_repair_dpkg"])
        self.assertFalse(report["machine_agent_task_auth_proven"])
        self.assertFalse(report["offline_console_and_rollback_proven"])

    def test_mcp_not_running_holds(self):
        units = all_healthy()
        units[audit.UNITS[0]]["ActiveState"] = "inactive"
        report = audit.evaluate(units, True)
        self.assertFalse(report["mcp_and_fleet_exec_service_checks_pass"])

    def test_relay_failure_holds(self):
        units = all_healthy()
        units[audit.UNITS[1]]["Result"] = "exit-code"
        self.assertFalse(audit.evaluate(units, True)["mcp_and_fleet_exec_service_checks_pass"])

    def test_device_uncontained_holds(self):
        units = all_healthy()
        units[audit.UNITS[2]]["NoNewPrivileges"] = "no"
        report = audit.evaluate(units, True)
        self.assertFalse(report["mcp_and_fleet_exec_service_checks_pass"])

    def test_execution_context_not_contained_holds(self):
        report = audit.evaluate(all_healthy(), False)
        self.assertIn("current_tool_execution_boundary_unverified", report["issues"])

    def test_missing_unit_data_holds(self):
        report = audit.evaluate({}, True)
        self.assertEqual(len(report["service_findings"]), 3)
        self.assertFalse(report["mcp_and_fleet_exec_service_checks_pass"])

    def test_parser_only_accepts_explicit_properties(self):
        data = audit.parse_unit_properties(
            "ActiveState=active\nEnvironment=SECRET\nNoNewPrivileges=yes\n"
        )
        self.assertNotIn("Environment", data)
        self.assertEqual(data["NoNewPrivileges"], "yes")

    def test_nonzero_unit_query_yields_empty_status(self):
        mock = Mock(return_value=SimpleNamespace(returncode=1, stdout="NoNewPrivileges=yes"))
        units = audit.get_units(mock)
        self.assertEqual(units[audit.UNITS[0]], {})
        self.assertEqual(mock.call_count, len(audit.UNITS))

    def test_unit_queries_are_fixed_and_do_not_use_shell(self):
        mock = Mock(return_value=SimpleNamespace(returncode=0, stdout="ActiveState=active"))
        audit.get_units(mock)
        for call in mock.call_args_list:
            argv = call.args[0]
            self.assertEqual(argv[:3], ["systemctl", "--user", "show"])
            self.assertNotIn("sudo", argv)
            self.assertNotIn("--property=Environment", argv)

    def test_timeout_does_not_claim_success(self):
        mock = Mock(side_effect=subprocess.TimeoutExpired("systemctl", 5))
        report = audit.evaluate(audit.get_units(mock), True)
        self.assertFalse(report["mcp_and_fleet_exec_service_checks_pass"])


if __name__ == "__main__":
    unittest.main()
