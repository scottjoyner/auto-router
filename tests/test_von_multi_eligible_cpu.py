"""Source-only tests: fictional multi-eligible objective and Von shadow contract."""
from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
M = importlib.import_module("von_multi_eligible_cpu")


class TestMultiEligible(unittest.TestCase):
    def test_twelve_profiles_balanced_and_complete(self):
        cases = M.build()
        M.validate_design(cases)
        self.assertEqual(len(cases), 48)
        self.assertEqual(len({r["case_id"] for r in cases}), 12)
        self.assertEqual(sum(r["gold"] == "node_a" for r in cases), 24)
        self.assertEqual(sum(r["gold"] == "node_b" for r in cases), 24)

    def test_all_cases_are_dual_admissible_by_simulated_rules_only(self):
        for x in M.build():
            self.assertEqual(x["eligible_options"], ["node_a", "node_b"])
            self.assertNotEqual(x["expected_a"], x["expected_b"])

    def test_raw_and_derived_share_facts_but_not_prompt(self):
        rows = M.build()
        for case in {r["case_id"] for r in rows}:
            group = [r for r in rows if r["case_id"] == case]
            self.assertEqual(len({r["facts_sha256"] for r in group}), 1)
            self.assertEqual(len({r["question_sha256"] for r in group}), 4)
            self.assertEqual(len({r["gold"] for r in group}), 1)

    def test_naive_heuristic_has_measurable_regret(self):
        profiles = M.PROFILES
        disagree = [p for p in profiles if M.oracle(p) != M.primitive_heuristic(p)]
        self.assertGreaterEqual(len(disagree), 1)

    def test_no_held_out_real_labels_claimed(self):
        rows = M.build()
        self.assertTrue(all(r["case_id"].startswith(("latency", "energy")) for r in rows))

    def test_changing_authority_between_variants_rejected(self):
        rows = M.build()
        rows[1]["eligible_options"] = ["node_a"]
        with self.assertRaises(ValueError):
            M.validate_design(rows)

    def test_missing_option_order_refused(self):
        with self.assertRaises(ValueError):
            M.validate_design(M.build()[:-1])

    def test_synthetic_full_coverage_analysis(self):
        observations = []
        for row in M.build():
            observations.append({
                "case_id": row["case_id"], "template": row["template"],
                "order": row["order"], "prediction": row["gold"],
                "gold": row["gold"], "primitive_choice": row["primitive_choice"],
                "regret_model": 0.0, "regret_primitive": 12.0,
                "confidence": .65, "decision_latency_ms": 120.0,
            })
        result = M.analyze(observations)
        self.assertEqual(result["order_pairs"], 24)
        self.assertEqual(result["order_changes"], 0)
        self.assertEqual(result["by_template"]["raw"]["model_correct"], 24)
        self.assertEqual(result["by_template"]["derived"]["model_correct"], 24)

    def test_dry_run_does_not_load_model(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            with patch.object(M, "run", side_effect=AssertionError("model call")), \
                    patch.object(M, "physical_preflight",
                                      return_value={"capacity_admitted": True}), \
                    patch.object(sys, "argv", ["pilot", "--workspace", d]):
                self.assertEqual(M.main(), 0)

    def test_execution_requires_ack(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            with patch.object(M, "run", side_effect=AssertionError("model call")), \
                    patch.object(sys, "argv", ["pilot", "--workspace", d, "--execute"]):
                self.assertEqual(M.main(), 2)

    def test_cpu_scope_required_before_model_weights(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            with patch.object(M, "physical_preflight", return_value={}), \
                    patch.object(M, "require_resource_scope",
                                      side_effect=ValueError("scope not bounded")):
                with self.assertRaisesRegex(ValueError, "scope not bounded"):
                    M.run(Path(d), Path(d) / "result.json")

    def test_external_output_path_refused(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            with patch.object(M, "physical_preflight", return_value={}), \
                    patch.object(M, "require_resource_scope", return_value=None):
                with self.assertRaises(ValueError):
                    M.run(Path(d), Path("/nas/unverified.json"))


if __name__ == "__main__":
    unittest.main()
