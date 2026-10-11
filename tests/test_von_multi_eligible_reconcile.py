"""Source-only verification of pinned dual-eligible choice report and unit separation."""
from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
R = importlib.import_module("von_multi_eligible_reconcile")
M = importlib.import_module("von_multi_eligible_cpu")


def fabricated_report() -> dict:
    rows = []
    for r in M.build():
        optimal = min(r["expected_a"], r["expected_b"])
        primitive = r["expected_a" if r["primitive_choice"] == "node_a" else "expected_b"]
        predicted = r["gold"]
        choice = r["expected_a" if predicted == "node_a" else "expected_b"]
        rows.append({
            "case_id": r["case_id"], "objective": r["objective"],
            "template": r["template"], "order": r["order"],
            "facts_sha256": r["facts_sha256"],
            "question_sha256": r["question_sha256"],
            "eligible_options": r["eligible_options"], "gold": r["gold"],
            "primitive_choice": r["primitive_choice"], "prediction": predicted,
            "confidence": .75, "decision_latency_ms": 312.0,
            "regret_model": round(choice - optimal, 5),
            "regret_primitive": round(primitive - optimal, 5),
            "expected_a": r["expected_a"], "expected_b": r["expected_b"],
        })
    return {
        "research_type": "fictional_two_eligible_objective_cpu_choice",
        "hf_revision": R.HF_REV, "weights_sha256": R.WEIGHTS.copy(),
        "cases": rows, "summary": M.analyze(rows),
        "synthetic_cases": True, "real_authority_evidence": False,
        "independent_labels": False, "signed_execution_attested": False,
        "safe_for_production": False, "new_router_policy": False,
        "cpu_model_calls": 48, "latency_and_energy_units_are_distinct": True,
    }


class TestMultiEligibleReconciliation(unittest.TestCase):
    def test_checks_complete_balanced_fictional_report(self):
        data = fabricated_report()
        self.assertEqual(len(R.verify(data)), 48)

    def test_rejects_missing_row(self):
        report = fabricated_report()
        report["cases"].pop()
        with self.assertRaises(ValueError):
            R.verify(report)

    def test_rejects_question_contract_tamper(self):
        report = fabricated_report()
        report["cases"][0]["question_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "contract changed"):
            R.verify(report)

    def test_rejects_new_private_prompt_field(self):
        report = fabricated_report()
        report["cases"][0]["raw_prompt"] = "private"
        with self.assertRaisesRegex(ValueError, "output data"):
            R.verify(report)

    def test_rejects_revision_claim(self):
        report = fabricated_report()
        report["hf_revision"] = "untrusted"
        with self.assertRaises(ValueError):
            R.verify(report)

    def test_rejects_fake_physical_witness(self):
        report = fabricated_report()
        report["signed_execution_attested"] = True
        with self.assertRaisesRegex(ValueError, "provenance"):
            R.verify(report)

    def test_rejects_fabricated_probability(self):
        report = fabricated_report()
        report["cases"][0]["confidence"] = float("nan")
        with self.assertRaisesRegex(ValueError, "confidence"):
            R.verify(report)

    def test_rejects_changed_regret(self):
        report = fabricated_report()
        report["cases"][0]["regret_model"] = 999
        with self.assertRaisesRegex(ValueError, "regret"):
            R.verify(report)

    def test_separates_joules_from_milliseconds(self):
        out = R.reconcile(R.verify(fabricated_report()))
        for template in ("raw", "derived"):
            self.assertEqual(set(out["breakdown"][template]), {"latency_ms", "energy_j"})
            self.assertTrue(out["objective_units_are_never_combined"])
            self.assertNotIn(
                "latency_gain_over_naive_after_model_decision_ms",
                out["breakdown"][template]["energy_j"],
            )
            self.assertIn(
                "latency_gain_over_naive_after_model_decision_ms",
                out["breakdown"][template]["latency_ms"],
            )

    def test_latency_overhead_subtracted_even_when_model_optimal(self):
        result = R.reconcile(R.verify(fabricated_report()))
        for template in ("raw", "derived"):
            self.assertLess(result["breakdown"][template]["latency_ms"][
                "latency_gain_over_naive_after_model_decision_ms"], 0)

    def test_no_real_world_authorization_claims(self):
        result = R.reconcile(R.verify(fabricated_report()))
        self.assertEqual(result["independently_labelled_real_cases"], 0)
        self.assertFalse(result["authenticated_real_eligibility_evidence"])
        self.assertFalse(result["production_admission_enabled"])

    def test_invalid_json_input_fails(self):
        with self.assertRaises(ValueError):
            R.verify({"cases": []})


if __name__ == "__main__":
    unittest.main()
