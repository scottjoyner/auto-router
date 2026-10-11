"""Source-only tests for the 40-prompt optional-abstention experiment."""
from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
A = importlib.import_module("von_abstention_ablation")


class TestAbstentionAblation(unittest.TestCase):
    def test_exact_paired_design(self):
        rows = A.build()
        A.validate_design(rows)
        self.assertEqual(len(rows), 40)
        self.assertEqual(sum(x["evidence_complete"] for x in rows), 32)
        self.assertEqual(sum(not x["evidence_complete"] for x in rows), 8)

    def test_only_defer_option_differs(self):
        groups: dict[tuple[str, str], list[dict]] = {}
        for row in A.build():
            groups.setdefault((row["case_id"], row["order"]), []).append(row)
        self.assertEqual(len(groups), 20)
        for pair in groups.values():
            forced = next(x for x in pair if x["condition"] == "forced_pair")
            optional = next(x for x in pair if x["condition"] == "abstain_available")
            self.assertEqual(forced["state"], optional["state"])
            self.assertEqual(forced["instructions"], optional["instructions"])
            self.assertEqual(list(forced["criteria"].items()),
                             [x for x in optional["criteria"].items() if x[0] != "defer"])

    def test_both_side_assignments_present(self):
        rows = A.build()
        forced = [r for r in rows if r["condition"] == "forced_pair"
                  and r["evidence_complete"]]
        self.assertEqual(sum(x["gold"] == "node_a" for x in forced), 8)
        self.assertEqual(sum(x["gold"] == "node_b" for x in forced), 8)

    def test_missing_evidence_never_permits_runtime(self):
        rows = A.build()
        self.assertTrue(all(not x["eligible_routes"] and x["gold"] == "defer"
                            for x in rows if not x["evidence_complete"]))

    def test_summary_rejects_incomplete_run(self):
        with self.assertRaisesRegex(ValueError, "incomplete experiment"):
            A.summarize([])

    def test_diagnostic_synthetic_counterfactual(self):
        observations = []
        for row in A.build():
            prediction = row["gold"]
            if row["condition"] == "abstain_available":
                prediction = "defer"
            if row["condition"] == "forced_pair" and not row["evidence_complete"]:
                prediction = "node_a"
            observations.append({
                key: row[key] for key in ("case_id", "order", "condition",
                                        "eligible_routes", "evidence_complete", "gold")
            } | {"prediction": prediction, "confidence": .8, "latency_ms": 20.0})
        stats = A.summarize(observations)
        self.assertEqual(stats["paired_actionable"], 16)
        self.assertEqual(stats["paired_forced_correct_defer_abstain"], 16)
        self.assertEqual(stats["conditions"]["forced_pair"]["correct_eligible_routes"], 16)
        self.assertEqual(stats["conditions"]["abstain_available"]["correct_eligible_routes"], 0)
        self.assertEqual(stats["conditions"]["abstain_available"]["safe_no_route_on_missing"], 4)

    def test_dry_run_does_not_call_model(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            with patch.object(A, "run", side_effect=AssertionError("no model")), \
                    patch.object(A, "physical_preflight",
                                      return_value={"capacity_admitted": True}), \
                    patch.object(sys, "argv", ["ablation", "--workspace", d]):
                self.assertEqual(A.main(), 0)

    def test_requires_explicit_ack(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            with patch.object(A, "run", side_effect=AssertionError("no model")), \
                    patch.object(sys, "argv", ["ablation", "--workspace", d,
                                              "--execute"]):
                self.assertEqual(A.main(), 2)

    def test_no_resource_scope_no_model(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            with patch.object(A, "physical_preflight", return_value={}), \
                    patch.object(A, "require_resource_scope",
                                      side_effect=ValueError("no hard scope")):
                with self.assertRaisesRegex(ValueError, "no hard scope"):
                    A.run(Path(d), Path(d) / "result.json")

    def test_refuse_output_outside_tmp(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            with patch.object(A, "physical_preflight", return_value={}), \
                    patch.object(A, "require_resource_scope", return_value=None):
                with self.assertRaises(ValueError):
                    A.run(Path(d), Path("/nas/forbidden.json"))


if __name__ == "__main__":
    unittest.main()
