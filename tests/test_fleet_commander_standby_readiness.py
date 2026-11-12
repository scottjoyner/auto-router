"""Isolated negative tests for standby control-plane safety boundaries."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock

P = Path(__file__).resolve().parents[1] / "research/fleet_commander_standby_readiness.py"
SPEC = importlib.util.spec_from_file_location("fleet_standby", P)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def populated():
    return {
        "hostname": "xwing",
        "candidate_known": True,
        "node_installed": True,
        "npm_installed": True,
        "ssh_installed": True,
        "tailscale_installed": True,
        "kvm_usable": True,
        "mcp_tunnel_unit_installed": True,
        "tunnel_client_installed": True,
        "desktop_commander_installed": True,
    }


class StandbyReadinessTests(unittest.TestCase):
    def test_even_complete_software_holds_for_independent_identity(self):
        result = mod.assess(populated(), True)
        self.assertFalse(result["standby_eligible_for_cutover"])
        self.assertFalse(result["may_start_tunnel"])
        self.assertFalse(result["may_copy_credentials"])
        self.assertFalse(result["may_modify_systemd"])
        self.assertFalse(result["may_change_control_plane_routing"])

    def test_missing_tunnel_binary_reported(self):
        facts = populated()
        facts["tunnel_client_installed"] = False
        self.assertIn("mcp_tunnel_client_not_provisioned", mod.assess(facts, True)["hold_reasons"])

    def test_missing_desktop_commander_reported(self):
        facts = populated()
        facts["desktop_commander_installed"] = False
        self.assertIn("mcp_server_not_provisioned", mod.assess(facts, True)["hold_reasons"])

    def test_missing_standby_unit_reported(self):
        facts = populated()
        facts["mcp_tunnel_unit_installed"] = False
        self.assertIn(
            "dedicated_standby_systemd_unit_not_provisioned",
            mod.assess(facts, True)["hold_reasons"],
        )

    def test_relay_down_does_not_claim_takeover(self):
        r = mod.assess(populated(), False)
        self.assertIn("existing_relay_transport_unreachable", r["hold_reasons"])
        self.assertFalse(r["standby_eligible_for_cutover"])

    def test_unapproved_host_holds(self):
        facts = populated()
        facts.update(hostname="other", candidate_known=False)
        self.assertIn("candidate_not_allowlisted", mod.assess(facts, True)["hold_reasons"])

    def test_missing_ssh_holds(self):
        facts = populated()
        facts["ssh_installed"] = False
        self.assertIn("ssh_client_missing", mod.assess(facts, True)["hold_reasons"])

    def test_missing_tailnet_holds(self):
        facts = populated()
        facts["tailscale_installed"] = False
        self.assertIn("tailnet_client_missing", mod.assess(facts, True)["hold_reasons"])

    def test_inspect_never_reads_key_content(self):
        tools = Mock(side_effect=lambda n: "/usr/bin/" + n)
        d = mod.inspect_host("xwing", Path("/not-a-real-home"), tools)
        self.assertTrue(d["candidate_known"])
        self.assertFalse(d["mcp_tunnel_unit_installed"])
        self.assertNotIn("credential_value", d)
        self.assertTrue(d["npm_installed"])

    def test_tcp_rejects_unapproved_endpoint_without_network(self):
        connect = Mock()
        self.assertFalse(mod.tcp_check("untrusted", mod.RELAY_PORT, connect=connect))
        connect.assert_not_called()

    def test_tcp_reachable_closes_socket(self):
        conn = Mock()
        s = Mock()
        conn.return_value = s
        self.assertTrue(mod.tcp_check(mod.RELAY_ADDRESS, mod.RELAY_PORT, connect=conn))
        s.close.assert_called_once()

    def test_tcp_timeout_never_claims_health(self):
        conn = Mock(side_effect=TimeoutError("timeout"))
        self.assertFalse(mod.tcp_check(mod.RELAY_ADDRESS, mod.RELAY_PORT, connect=conn))

    def test_missing_node_tools_hold(self):
        facts = populated()
        facts["node_installed"] = False
        facts["npm_installed"] = False
        reasons = mod.assess(facts, True)["hold_reasons"]
        self.assertIn("nodejs_missing", reasons)
        self.assertIn("npm_missing", reasons)


if __name__ == "__main__":
    unittest.main()
