"""Source-only safety tests for the separately scoped physical CPU pilot.

No model weights, networks, SSH, GPU, or Von imports are used.
"""
from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
P = importlib.import_module("von_cpu_pilot")
C = importlib.import_module("von_cpu_controls")


class TestPhysicalCpuPilotGuardrails(unittest.TestCase):
    def test_checkpoint_revision_and_digests_pinned(self):
        self.assertEqual(len(P.HF_REV), 40)
        self.assertEqual(set(P.WEIGHTS), {"option_marker.pt", "model.safetensors"})
        self.assertTrue(all(len(digest) == 64 for digest in P.WEIGHTS.values()))

    def test_exact_resource_quota_accepted(self):
        P.validate_scope(str(8 * 1024 ** 3), "150000 100000")
        P.validate_scope(str(4 * 1024 ** 3), "100000 100000")

    def test_unbounded_memory_refused(self):
        with self.assertRaises(ValueError):
            P.validate_scope("max", "150000 100000")

    def test_excessive_memory_refused(self):
        with self.assertRaises(ValueError):
            P.validate_scope(str(10 * 1024 ** 3), "150000 100000")

    def test_excessive_cpu_refused(self):
        with self.assertRaises(ValueError):
            P.validate_scope(str(8 * 1024 ** 3), "200000 100000")

    def test_unlimited_cpu_refused(self):
        with self.assertRaises(ValueError):
            P.validate_scope(str(8 * 1024 ** 3), "max 100000")

    def test_refuse_storage_outside_tmp(self):
        with self.assertRaises(ValueError):
            P.admissible_workspace(Path("/nas/von-research"))

    def test_refuse_workspace_symlink(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            link = Path(d) / "shortcut"
            actual = Path(d) / "real"
            actual.mkdir()
            link.symlink_to(actual, target_is_directory=True)
            with self.assertRaises(ValueError):
                P.admissible_workspace(link)

    def test_no_repeated_checkpoint_without_new_cache(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            work = Path(d)
            (work / "hf-cache").mkdir()
            with patch.object(P, "physical_preflight", return_value={}), \
                    patch.object(P, "require_resource_scope", return_value=None):
                with self.assertRaises(ValueError):
                    P.prefetch(work)

    def test_missing_resource_scope_blocks_weight_fetch(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            with patch.object(P, "physical_preflight", return_value={}), \
                    patch.object(P, "require_resource_scope",
                                      side_effect=ValueError("not in bounded cgroup")):
                with self.assertRaisesRegex(ValueError, "bounded cgroup"):
                    P.prefetch(Path(d))

    def test_control_cases_are_fictional_and_stable(self):
        self.assertEqual(len(C.CASES), 8)
        self.assertEqual(len({x[0] for x in C.CASES}), 8)
        self.assertEqual(sum(x[1] == "routing" for x in C.CASES), 4)
        for case in C.CASES:
            self.assertIn(case[-1], case[4])

    def test_default_mode_has_no_inference(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            with patch.object(C, "run", side_effect=AssertionError("no model")), \
                    patch.object(C, "physical_preflight",
                                      return_value={"capacity_admitted": True}), \
                    patch.object(sys, "argv", ["controls", "--workspace", d]):
                self.assertEqual(C.main(), 0)

    def test_execute_requires_ack_and_output(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            with patch.object(C, "run", side_effect=AssertionError("should not run")), \
                    patch.object(sys, "argv", ["controls", "--execute",
                                              "--workspace", d]):
                self.assertEqual(C.main(), 2)

    def test_missing_checkpoint_fails_before_inference(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            with self.assertRaisesRegex(ValueError, "snapshot"):
                P.check_snapshot(Path(d) / "not-loaded")

    def test_modified_checkpoint_hash_refused(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            snapshot = Path(d)
            for name in P.REQUIRED:
                (snapshot / name).write_bytes(b"not the model")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                P.check_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
