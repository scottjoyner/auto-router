"""No-network synthetic tests for independent, unprivileged SSH probe."""

from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / "research/x1_backchannel_readiness.py"
SPEC = importlib.util.spec_from_file_location("x1_backchannel", SOURCE)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def response(stdout="x1-370\n7.0.0-42-generic\n", returncode=0):
    return SimpleNamespace(stdout=stdout, returncode=returncode)


class BackchannelTests(unittest.TestCase):
    def test_valid_ssh_is_reachable_but_never_authorizes_repair(self):
        runner = Mock(return_value=response())
        result = probe.probe("x1-370", runner)
        self.assertTrue(result["reachable"])
        self.assertTrue(result["host_identity_matched"])
        self.assertFalse(result["out_of_band_console_verified"])
        self.assertFalse(result["may_elevate_privileges"])
        self.assertFalse(result["may_repair_os"])
        self.assertFalse(result["may_reboot"])

    def test_strict_noninteractive_ssh_flags(self):
        runner = Mock(return_value=response())
        probe.probe("x1-370", runner)
        argv = runner.call_args.args[0]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("PasswordAuthentication=no", argv)
        self.assertIn("ClearAllForwardings=yes", argv)
        self.assertEqual(argv[-1], "hostname; uname -r")
        self.assertNotIn("sudo", " ".join(argv))

    def test_wrong_hostname_holds(self):
        result = probe.probe("x1-370", Mock(return_value=response("other\n7.0.0-42-generic\n")))
        self.assertFalse(result["reachable"])
        self.assertEqual(result["reason"], "remote_hostname_mismatch")

    def test_empty_reply_holds(self):
        result = probe.probe("x1-370", Mock(return_value=response("")))
        self.assertFalse(result["reachable"])

    def test_nonzero_ssh_holds_without_leaking_stderr(self):
        result = probe.probe("x1-370", Mock(return_value=response("credential-secret", 255)))
        self.assertEqual(result["reason"], "ssh_unavailable_or_untrusted")
        self.assertNotIn("credential-secret", str(result))

    def test_timeout_holds(self):
        runner = Mock(side_effect=subprocess.TimeoutExpired("ssh", 7))
        result = probe.probe("x1-370", runner)
        self.assertFalse(result["reachable"])
        self.assertEqual(result["reason"], "ssh_probe_timeout")

    def test_unapproved_target_never_calls_ssh(self):
        runner = Mock()
        result = probe.probe("evil.example", runner)
        self.assertFalse(result["reachable"])
        self.assertEqual(result["reason"], "target_not_allowlisted")
        runner.assert_not_called()

    def test_malformed_kernel_holds(self):
        result = probe.probe("x1-370", Mock(return_value=response("x1-370\nnot valid\n")))
        self.assertFalse(result["reachable"])

    def test_additional_output_holds(self):
        result = probe.probe(
            "x1-370", Mock(return_value=response("x1-370\n7.0.0-42-generic\nthird\n"))
        )
        self.assertEqual(result["reason"], "unexpected_remote_reply")


if __name__ == "__main__":
    unittest.main()
