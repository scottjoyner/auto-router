"""Offline security and counterfactual tests; no real weights or routing API."""
from __future__ import annotations

import hashlib
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
E = importlib.import_module("von_policy_value_reconcile")


def fake_report(kind: str) -> dict:
    if kind == "wording":
        original = E.wording_cases()
    else:
        original = E.ablation_cases()
    obs = []
    for row in original:
        decision = row["gold"]
        if kind == "ablation" and row["condition"] == "forced_pair" and decision == "defer":
            decision = "node_b"
        datum = {field: row[field] for field in E.FIXED_KEYS[kind]}
        datum.update(prediction=decision, confidence=.7, latency_ms=12.0)
        if kind == "wording":
            datum["correct"] = decision == row["gold"]
        obs.append(datum)
    if kind == "wording":
        return dict(
            research_type="fictional_matched_wording_cpu_inference",
            hf_commit=E.HF_REV, checkpoint_sha256=E.WEIGHTS.copy(),
            total_model_calls=60, observations=obs,
            summary=E.wording_summary(obs), source_is_fictional=True,
            independent_labels=False, signed_execution_attested=False,
            safe_for_production=False, route_execution_attempted=False,
            confidence_is_not_admission=True,
        )
    return dict(
        research_type="fictional_two_vs_three_choice_cpu_ablation",
        model="von-1.3.0", hf_revision=E.HF_REV,
        weight_hashes=E.WEIGHTS.copy(), observations=obs,
        summary=E.ablation_summary(obs), actual_model_calls=40,
        signed_execution_attested=False, independent_labels=False,
        real_fleet_data=False, safe_for_production=False, routing_changes=False,
    )


class TestReconciledPolicyValue(unittest.TestCase):
    def test_default_deterministic_policy_never_mints_authority(self):
        self.assertEqual(E.deterministic_policy([]), "defer")
        self.assertEqual(E.deterministic_policy(["node_a"]), "node_a")
        self.assertEqual(E.deterministic_policy(["node_b"]), "node_b")
        self.assertEqual(E.deterministic_policy(["node_a", "node_b"]), "defer")
        self.assertEqual(E.deterministic_policy(["untrusted"]), "defer")

    def test_gate_never_promotes_unauthorized_model_choice(self):
        self.assertEqual(E.gate_model_answer("node_b", ["node_a"]), "defer")
        self.assertEqual(E.gate_model_answer("node_a", []), "defer")
        self.assertEqual(E.gate_model_answer("node_a", ["node_a"]), "node_a")

    def test_fake_reports_match_registered_fictional_cases(self):
        for kind in ("wording", "ablation"):
            data = fake_report(kind)
            rows = E.validate_records(data, kind)
            self.assertEqual(len(rows), 60 if kind == "wording" else 40)

    def test_cross_model_source_drift_rejected(self):
        d = fake_report("wording")
        d["observations"][0]["state_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "pinned fictional"):
            E.validate_records(d, "wording")

    def test_question_contract_drift_rejected(self):
        d = fake_report("ablation")
        d["observations"][0]["question_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "pinned fictional"):
            E.validate_records(d, "ablation")

    def test_wrong_model_weights_rejected(self):
        d = fake_report("wording")
        d["checkpoint_sha256"]["option_marker.pt"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            E.validate_records(d, "wording")

    def test_claim_of_independent_label_review_rejected(self):
        d = fake_report("ablation")
        d["independent_labels"] = True
        with self.assertRaisesRegex(ValueError, "trust"):
            E.validate_records(d, "ablation")

    def test_invalid_float_nan_rejected(self):
        d = fake_report("wording")
        d["observations"][0]["confidence"] = float("nan")
        with self.assertRaisesRegex(ValueError, "probability"):
            E.validate_records(d, "wording")

    def test_reported_aggregate_recalculated(self):
        d = fake_report("ablation")
        d["summary"]["paired_actionable"] = 15
        with self.assertRaisesRegex(ValueError, "aggregates"):
            E.validate_records(d, "ablation")

    def test_raw_private_field_refused(self):
        d = fake_report("wording")
        d["observations"][0]["raw_state"] = "not allowed"
        with self.assertRaisesRegex(ValueError, "metadata"):
            E.validate_records(d, "wording")

    def test_extra_or_duplicate_case_refused(self):
        d = fake_report("ablation")
        d["observations"][1]["case_id"] = d["observations"][0]["case_id"]
        d["observations"][1]["order"] = d["observations"][0]["order"]
        d["observations"][1]["condition"] = d["observations"][0]["condition"]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            E.validate_records(d, "ablation")

    def test_deterministic_baseline_solves_only_synthetic_singleton_cases(self):
        w = fake_report("wording")["observations"]
        a = fake_report("ablation")["observations"]
        x = E.comparison(w, a)
        self.assertEqual(x["source_observation_count"], 100)
        self.assertEqual(x["unique_fictional_situations_total"], 10)
        self.assertFalse(x["hardware_execution_attested"])
        self.assertFalse(x["production_approval"])
        self.assertEqual(x["by_template"]["explicit"]["deterministic_policy"][
            "correct_actionable"], 16)
        self.assertEqual(x["by_condition"]["forced_pair"]["deterministic_policy"][
            "unsafe_runtime_suggestions"], 0)
        self.assertEqual(x["by_condition"]["forced_pair"]["model_raw"][
            "unsafe_runtime_suggestions"], 4)

    def test_gate_removes_false_model_proposal_but_not_absention(self):
        rows = fake_report("wording")["observations"]
        one = next(x for x in rows if x["eligible_routes"])
        one["prediction"] = "node_b" if one["gold"] == "node_a" else "node_a"
        m = E.metrics([one], group="test")
        self.assertEqual(m["model_raw"]["unsafe_runtime_suggestions"], 1)
        self.assertEqual(m["model_proposal_gated"]["unsafe_runtime_suggestions"], 0)
        self.assertEqual(m["model_proposal_gated"]["defer_despite_eligible"], 1)

    def test_unpinned_file_digest_fails(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            p = Path(d) / "unexpected.json"
            p.write_text(json.dumps(fake_report("wording")))
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                E.checked_json(p, "wording")

    def test_exact_digest_guard_can_be_tested_with_fixture(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            p = Path(d) / "fake.json"
            raw = json.dumps(fake_report("ablation")).encode()
            p.write_bytes(raw)
            with patch.dict(E.REPORT_DIGESTS, {"ablation": hashlib.sha256(raw).hexdigest()}):
                parsed, digest = E.checked_json(p, "ablation")
                self.assertEqual(parsed["actual_model_calls"], 40)
                self.assertEqual(len(digest), 64)

    def test_direct_read_does_not_execute_model(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            p = Path(d) / "no_report.json"
            p.write_text("{}")
            with patch.object(E, "comparison",
                              side_effect=AssertionError("no untrusted comparison")), \
                    patch.object(sys, "argv", ["reconciler", "--wording", str(p),
                                              "--ablation", str(p)]):
                self.assertEqual(E.main(), 2)


if __name__ == "__main__":
    unittest.main()
