"""Mechanical, fictional policy-oracle corpus validation; never calls a model."""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))

B = importlib.import_module("build_synthetic_decision_challenge")
E = importlib.import_module("typed_decision_shadow")
V = importlib.import_module("von_choice_shadow")


class TestMechanicalChallenge(unittest.TestCase):
    def test_counts_and_fixed_policy_labels(self):
        cases, oracle = B.build()
        B.validate_generated(cases, oracle)
        self.assertEqual(len(cases), 216)
        self.assertEqual(sum(c["variant"] == "original" for c in cases), 144)
        self.assertEqual(sum(c["variant"] == "permuted" for c in cases), 72)
        self.assertEqual(sum(c["variant"] == "original" and c["evidence_complete"]
                             for c in cases), 128)
        self.assertEqual(sum(c["variant"] == "original" and not c["evidence_complete"]
                             for c in cases), 16)

    def test_cases_pass_input_validation(self):
        cases, oracle = B.build()
        for case in cases:
            self.assertEqual(V.validate_case(case), case)
        self.assertTrue(all(r["source_kind"] == "synthetic" for r in oracle))
        self.assertFalse(any(r["latency_ms"] is not None for r in oracle))

    def test_oracle_is_structural_sanity_not_model_benchmark(self):
        _, oracle = B.build()
        result = E.score(oracle, "policy_oracle")
        row = result["models"]["policy_oracle"]
        self.assertEqual(row["original_cases"], 144)
        self.assertEqual(row["actionable_cases"], 128)
        self.assertEqual(row["correct"], 128)
        self.assertIsNone(row["ece_10_bins"])
        self.assertEqual(row["latency_ms"]["n"], 0)
        self.assertFalse(result["execution_attested"])
        self.assertFalse(result["promotion_approved"])

    def test_reproducible_without_randomized_labels(self):
        cases1, oracle1 = B.build()
        cases2, oracle2 = B.build()
        self.assertEqual(
            json.dumps(cases1, sort_keys=True), json.dumps(cases2, sort_keys=True)
        )
        self.assertEqual(
            json.dumps(oracle1, sort_keys=True), json.dumps(oracle2, sort_keys=True)
        )

    def test_outputs_restrict_path_and_never_overwrite(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            path = Path(d) / "oracle.jsonl"
            B.write_new(path, [{"test": "fictional"}])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                B.write_new(path, [{"test": "changed"}])
        with self.assertRaisesRegex(ValueError, "under /tmp"):
            B.write_new(Path("/nas/decisions.jsonl"), [{"test": "fictional"}])

    def test_end_to_end_files_join_without_actual_model(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            cases_path = Path(d) / "cases.jsonl"
            baseline_path = Path(d) / "oracle.jsonl"
            with patch.object(sys, "argv", ["generate", "--cases-out", str(cases_path),
                                             "--oracle-out", str(baseline_path)]):
                self.assertEqual(B.main(), 0)
            cases, _ = V.fixture(cases_path)
            rows, _ = E.load(baseline_path)
            self.assertEqual(len(cases), len(rows))
            self.assertEqual(len(cases), 216)

    def test_dry_run_large_corpus_has_zero_http_requests(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            cases_path = Path(d) / "cases.jsonl"
            cases, _ = B.build()
            B.write_new(cases_path, cases)
            with patch.object(V, "request_von", side_effect=AssertionError("no network")), \
                    patch.object(sys, "argv", ["adapter", str(cases_path)]):
                self.assertEqual(V.main(), 0)

    def test_large_corpus_requires_explicit_request_budget(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            cases_path = Path(d) / "cases.jsonl"
            out_path = Path(d) / "observations.jsonl"
            cases, _ = B.build()
            B.write_new(cases_path, cases)
            with patch.object(V, "request_von", side_effect=AssertionError("no network")), \
                    patch.object(V, "verify_von_service",
                                      side_effect=AssertionError("no service preflight")), \
                    patch.object(sys, "argv", ["adapter", str(cases_path), "--execute",
                                              "--ack-sanitized", "--ack-preloaded",
                                              "--url", "http://127.0.0.1:18551",
                                              "--out", str(out_path)]):
                self.assertEqual(V.main(), 2)
                self.assertFalse(out_path.exists())


if __name__ == "__main__":
    unittest.main()
