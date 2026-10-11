"""No-model deterministic tests for the pinned Von 60-call matched wording study."""
from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
M = importlib.import_module("von_matched_wording")


class TestMatchedWording(unittest.TestCase):
    def test_design_has_sixty_variants(self):
        cases = M.cases()
        M.validate_design(cases)
        self.assertEqual(len(cases), 60)
        self.assertEqual(len({r["case_id"] for r in cases}), 10)
        self.assertEqual(sum(r["evidence_complete"] for r in cases), 48)
        self.assertEqual(sum(not r["evidence_complete"] for r in cases), 12)

    def test_ablated_question_keeps_exact_state(self):
        rows = M.cases()
        for case in {x["case_id"] for x in rows}:
            variants = [x for x in rows if x["case_id"] == case]
            self.assertEqual(len({x["state_sha256"] for x in variants}), 1)
            self.assertEqual(len({x["question_sha256"] for x in variants}), 6)
            self.assertEqual(len({x["gold"] for x in variants}), 1)

    def test_no_authority_never_has_routing_eligibility(self):
        cases = M.cases()
        no_proof = [r for r in cases if not r["evidence_complete"]]
        self.assertEqual(len(no_proof), 12)
        self.assertTrue(all(r["eligible_routes"] == [] for r in no_proof))
        self.assertTrue(all(r["gold"] == M.ABSTAIN for r in no_proof))

    def test_inverse_polarities_balanced(self):
        cases = [r for r in M.cases() if r["order"] == "forward"
                 and r["template"] == "terse" and r["evidence_complete"]]
        self.assertEqual(sum(r["gold"] == "node_a" for r in cases), 4)
        self.assertEqual(sum(r["gold"] == "node_b" for r in cases), 4)

    def test_permuting_presentation_is_not_mutating_authority(self):
        rows = M.cases()
        pair = [r for r in rows if r["case_id"] == "fictional_lease_node_b"
                and r["template"] == "explicit"]
        self.assertEqual(len(pair), 2)
        self.assertEqual(pair[0]["gold"], pair[1]["gold"])
        self.assertEqual(pair[0]["eligible_routes"], pair[1]["eligible_routes"])
        self.assertEqual(pair[0]["state"], pair[1]["state"])
        self.assertNotEqual(pair[0]["question_sha256"], pair[1]["question_sha256"])

    def test_missing_reverse_order_fails(self):
        rows = M.cases()[:-1]
        with self.assertRaisesRegex(ValueError, "sixty"):
            M.validate_design(rows)

    def test_fabricated_choice_aggregates_label_and_admission_separately(self):
        fabricated = []
        for row in M.cases():
            prediction = row["gold"]
            if row["family"] == "missing" and row["template"] == "terse":
                prediction = "node_a"
            fabricated.append({
                "case_id": row["case_id"], "family": row["family"],
                "template": row["template"], "order": row["order"],
                "gold": row["gold"], "eligible_routes": row["eligible_routes"],
                "evidence_complete": row["evidence_complete"],
                "prediction": prediction, "confidence": 0.73,
                "latency_ms": 1.0,
            })
        outcome = M.summarize(fabricated)
        self.assertEqual(outcome["order_pairs"], 30)
        self.assertEqual(outcome["order_changes"], 0)
        self.assertEqual(outcome["by_template"]["terse"]["unsafe_route_on_missing"], 4)
        self.assertEqual(outcome["by_template"]["explicit"]["safe_defer_on_missing"], 4)
        self.assertEqual(outcome["by_template"]["authority"]["correct_routes"], 16)

    def test_dry_run_never_imports_or_executes_model(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as path:
            with patch.object(M, "run", side_effect=AssertionError("must not call")), \
                    patch.object(M, "physical_preflight",
                                      return_value={"capacity_admitted": True}), \
                    patch.object(sys, "argv", ["prog", "--workspace", path]):
                self.assertEqual(M.main(), 0)

    def test_execution_requires_explicit_ack(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as path:
            with patch.object(M, "run", side_effect=AssertionError("must not call")), \
                    patch.object(sys, "argv", ["prog", "--workspace", path,
                                              "--execute"]):
                self.assertEqual(M.main(), 2)

    def test_without_cgroup_scope_model_not_loaded(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as path:
            with patch.object(M, "physical_preflight", return_value={}), \
                    patch.object(M, "require_resource_scope",
                                      side_effect=ValueError("no controlled scope")):
                with self.assertRaisesRegex(ValueError, "controlled scope"):
                    M.run(Path(path), Path(path) / "results.json")

    def test_writing_outside_tmp_rejected(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as path:
            with patch.object(M, "physical_preflight", return_value={}), \
                    patch.object(M, "require_resource_scope", return_value=None):
                with self.assertRaises(ValueError):
                    M.run(Path(path), Path("/nas/decisions.json"))

    def test_synthetic_cases_have_only_defer_for_no_evidence(self):
        fake = []
        for item in M.cases():
            fake.append({
                "case_id": item["case_id"], "family": item["family"],
                "template": item["template"], "order": item["order"],
                "gold": item["gold"], "eligible_routes": item["eligible_routes"],
                "evidence_complete": item["evidence_complete"],
                "prediction": M.ABSTAIN, "confidence": 0.8, "latency_ms": 20.0,
            })
        stats = M.summarize(fake)
        self.assertEqual(stats["by_template"]["explicit"]["safe_defer_on_missing"], 4)
        self.assertEqual(stats["by_template"]["explicit"]["correct_routes"], 0)
        self.assertEqual(stats["by_template"]["explicit"]["defer_when_route_exists"], 16)


if __name__ == "__main__":
    unittest.main()
