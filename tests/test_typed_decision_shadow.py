"""No model loads, routing calls, network access, or production writes."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "experiments" / "typed_decision_shadow.py"
SPEC = importlib.util.spec_from_file_location("decision_shadow", SOURCE)
E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E)


def row(**update):
    data = {
        "case_id": "case1", "model_id": "baseline", "model_revision": "fixed1",
        "task_type": "choice", "variant": "original", "state_sha256": "0" * 64,
        "gold": "node_a", "prediction": "node_a", "confidence": .9,
        "eligible_options": ["node_a", "node_b"], "evidence_complete": True,
        "latency_ms": 2.0, "status": "ok", "source_kind": "synthetic",
    }
    data.update(update)
    return data


def load_data(data):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "observations.jsonl"
        p.write_text("".join(json.dumps(x) + "\n" for x in data), encoding="utf-8")
        return E.load(p)


class TestDecisionShadow(unittest.TestCase):
    def test_no_extra_content_fields(self):
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            E.check(row(raw_prompt="do not export"))

    def test_bounded_input(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "too-big.jsonl"
            p.write_text("x" * (16_000_001))
            with self.assertRaisesRegex(ValueError, "16 MB"):
                E.load(p)

    def test_validated_prediction(self):
        self.assertEqual(E.check(row())["prediction"], "node_a")

    def test_wrong_gold_requires_independent_label(self):
        with self.assertRaisesRegex(ValueError, "gold label"):
            E.check(row(gold="unknown"))

    def test_nonfinite_confidence_rejected(self):
        with self.assertRaisesRegex(ValueError, "confidence"):
            E.check(row(confidence=float("inf")))

    def test_nonfinite_latency_rejected(self):
        with self.assertRaisesRegex(ValueError, "latency"):
            E.check(row(latency_ms=float("nan")))

    def test_error_cannot_claim_prediction(self):
        with self.assertRaisesRegex(ValueError, "prediction"):
            E.check(row(status="unavailable"))

    def test_missing_evidence_variant_must_be_incomplete(self):
        with self.assertRaisesRegex(ValueError, "missing-evidence"):
            E.check(row(variant="missing_evidence"))

    def test_bad_choice_counted_as_unsafe(self):
        outcome = E.score([E.check(row(prediction="unavailable-node"))], "baseline")
        self.assertEqual(outcome["models"]["baseline"]["ineligible_outputs"], 1)
        self.assertFalse(outcome["promotion_approved"])

    def test_missing_evidence_suggestions_counted(self):
        outcome = E.score([E.check(row(evidence_complete=False, gold=None))], "baseline")
        self.assertEqual(outcome["models"]["baseline"]["missing_evidence_outputs"], 1)
        self.assertIsNone(outcome["models"]["baseline"]["accuracy_on_answered"])

    def test_unavailable_is_not_success(self):
        outcome = E.score([E.check(row(status="unavailable", prediction=None, confidence=None))],
                          "baseline")
        self.assertIsNone(outcome["models"]["baseline"]["coverage"] if
                          outcome["models"]["baseline"]["actionable_cases"] == 0 else
                          outcome["models"]["baseline"]["accuracy_on_answered"])
        self.assertEqual(outcome["models"]["baseline"]["unavailable_or_error"], 1)

    def test_duplicate_cases_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_data([row(), row()])

    def test_cross_model_label_drift_rejected(self):
        with self.assertRaisesRegex(ValueError, "case contract"):
            load_data([row(), row(model_id="qsiv", gold="node_b")])

    def test_revision_mixing_rejected(self):
        with self.assertRaisesRegex(ValueError, "revisions mixed"):
            load_data([row(), row(case_id="case2", model_revision="different")])

    def test_source_spoofing_cohort_mixing_rejected(self):
        with self.assertRaisesRegex(ValueError, "source kinds mixed"):
            load_data([row(), row(case_id="case2", source_kind="model_observation")])

    def test_missing_permutation_anchor_rejected(self):
        with self.assertRaisesRegex(ValueError, "permutation"):
            load_data([row(variant="permuted")])

    def test_wrong_permutation_digest_rejected(self):
        with self.assertRaisesRegex(ValueError, "permutation"):
            load_data([row(), row(variant="permuted", state_sha256="f" * 64)])

    def test_permutation_stability_count(self):
        rows, _ = load_data([row(), row(variant="permuted", prediction="node_b")])
        outcome = E.score(rows, "baseline")
        self.assertEqual(outcome["models"]["baseline"]["permutation_changed"], 1)
        self.assertEqual(outcome["models"]["baseline"]["answered"], 1)

    def test_exact_p_value(self):
        self.assertEqual(E.mcnemar(0, 0), 1)
        self.assertEqual(E.mcnemar(5, 5), 1)
        self.assertLess(E.mcnemar(40, 10), .05)

    def test_paired_comparison(self):
        rows, _ = load_data([
            row(), row(model_id="qsiv", prediction="node_b"),
            row(case_id="case2", gold="node_b", prediction="node_a"),
            row(case_id="case2", model_id="qsiv", gold="node_b", prediction="node_b")])
        compared = E.score(rows, "baseline")["comparisons_to_baseline"]["qsiv"]
        self.assertEqual(compared["paired_valid"], 2)
        self.assertEqual(compared["baseline_only_correct"], 1)
        self.assertEqual(compared["candidate_only_correct"], 1)
        self.assertFalse(compared["canary_authorized"])


if __name__ == "__main__":
    unittest.main()
