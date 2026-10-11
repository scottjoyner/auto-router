"""Exhaustive safety checks for purely fictional fleet cost shadow evaluation."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fleet_cost_shadow", ROOT / "experiments" / "fleet_cost_shadow.py"
)
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)
FIXTURE = ROOT / "experiments" / "fixtures" / "fleet_cost_fictional.json"


def sample() -> dict:
    return json.loads(FIXTURE.read_text())


def scored(data: dict) -> dict:
    return M.rank(copy.deepcopy(data))


class TestCostShadowAuthority(unittest.TestCase):
    def test_latency_prefers_fastest_bound(self):
        value = scored(sample())
        self.assertEqual(value["status"], "RANKED_SYNTHETIC_ONLY")
        self.assertEqual(value["ranked_synthetic_ids"], ["runtime_a", "runtime_b"])
        self.assertEqual(value["eligible_count"], 2)
        self.assertEqual(value["rank_costs"][0]["latency_upper_ms"], 10100)

    def test_energy_prefers_lower_total_joules(self):
        doc = sample()
        doc["request"]["objective"] = "energy_j"
        result = scored(doc)
        self.assertEqual(result["ranked_synthetic_ids"], ["runtime_b", "runtime_a"])
        self.assertEqual(result["rank_costs"][0]["energy_upper_j"], 765.0)

    def test_source_never_claims_real_authority(self):
        value = scored(sample())
        self.assertFalse(value["authority_to_dispatch"])
        self.assertFalse(value["physical_execution_attested"])
        self.assertFalse(value["production_promotion_approved"])
        self.assertEqual(value["input_kind"], "fictional-only")

    def test_reject_live_projection_as_unverified(self):
        doc = sample()
        doc["kind"] = "verified_live"
        with self.assertRaisesRegex(ValueError, "not accepted"):
            scored(doc)

    def test_unknown_raw_payload_rejected(self):
        doc = sample()
        doc["raw_prompt"] = "sensitive"
        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            scored(doc)

    def test_unknown_request_metadata_rejected(self):
        doc = sample()
        doc["request"]["auth_bearer_token"] = "forbidden"
        with self.assertRaisesRegex(ValueError, "request schema"):
            scored(doc)

    def test_unknown_telemetry_provenance_field_rejected(self):
        doc = sample()
        doc["telemetry"][0]["unreviewed_source"] = "user supplied"
        with self.assertRaisesRegex(ValueError, "telemetry evidence"):
            scored(doc)

    def test_duplicate_or_conflicting_runtime_rejected_whole_response(self):
        doc = sample()
        doc["runtimes"].append(copy.deepcopy(doc["runtimes"][0]))
        with self.assertRaisesRegex(ValueError, "duplicated"):
            scored(doc)

    def test_duplicate_telemetry_rejected_whole_response(self):
        doc = sample()
        doc["telemetry"].append(copy.deepcopy(doc["telemetry"][0]))
        with self.assertRaisesRegex(ValueError, "duplicated"):
            scored(doc)

    def test_unbound_telemetry_rejected_whole_response(self):
        doc = sample()
        doc["telemetry"][0]["runtime_id"] = "other"
        with self.assertRaisesRegex(ValueError, "matching runtime"):
            scored(doc)

    def test_stale_authority_bundle_rejected(self):
        doc = sample()
        doc["authority_observed_ms"] -= 60_000
        with self.assertRaisesRegex(ValueError, "stale"):
            scored(doc)

    def test_future_authority_rejected(self):
        doc = sample()
        doc["authority_observed_ms"] += 9_000
        with self.assertRaisesRegex(ValueError, "future"):
            scored(doc)

    def test_expired_snapshot_rejected(self):
        doc = sample()
        doc["authority_expires_ms"] = doc["as_of_ms"]
        with self.assertRaisesRegex(ValueError, "expired"):
            scored(doc)

    def test_excessive_snapshot_validity_rejected(self):
        doc = sample()
        doc["authority_expires_ms"] += 40_000
        with self.assertRaisesRegex(ValueError, "excessively"):
            scored(doc)

    def test_offline_eligible_candidate_is_disqualified(self):
        doc = sample()
        doc["runtimes"][0]["health"] = "OFFLINE"
        result = scored(doc)
        self.assertEqual(result["eligible_count"], 1)
        self.assertEqual(result["ranked_synthetic_ids"], ["runtime_b"])

    def test_available_is_not_loaded(self):
        doc = sample()
        doc["runtimes"][0]["loaded_state"] = "AVAILABLE"
        result = scored(doc)
        self.assertEqual(result["ranked_synthetic_ids"], ["runtime_b"])

    def test_unknown_health_never_inferred_healthy(self):
        doc = sample()
        doc["runtimes"][0]["health"] = "UNKNOWN"
        self.assertEqual(scored(doc)["ranked_synthetic_ids"], ["runtime_b"])

    def test_zero_available_slots_not_admitted(self):
        doc = sample()
        doc["runtimes"][0]["free_slots"] = 0
        self.assertEqual(scored(doc)["ranked_synthetic_ids"], ["runtime_b"])

    def test_owner_mismatch_disqualified(self):
        doc = sample()
        doc["runtimes"][0]["owner_id"] = "owner2"
        self.assertEqual(scored(doc)["ranked_synthetic_ids"], ["runtime_b"])

    def test_request_mismatch_disqualified(self):
        doc = sample()
        doc["runtimes"][0]["request_id"] = "request2"
        self.assertEqual(scored(doc)["ranked_synthetic_ids"], ["runtime_b"])

    def test_model_mismatch_disqualified(self):
        doc = sample()
        doc["runtimes"][0]["model_id"] = "other"
        self.assertEqual(scored(doc)["ranked_synthetic_ids"], ["runtime_b"])

    def test_task_not_in_capabilities_disqualified(self):
        doc = sample()
        doc["runtimes"][0]["capabilities"] = ["image"]
        self.assertEqual(scored(doc)["ranked_synthetic_ids"], ["runtime_b"])

    def test_expired_lease_is_not_admitted(self):
        doc = sample()
        doc["runtimes"][0]["lease_until_ms"] = doc["as_of_ms"]
        self.assertEqual(scored(doc)["ranked_synthetic_ids"], ["runtime_b"])

    def test_lease_not_yet_active(self):
        doc = sample()
        doc["runtimes"][0]["lease_not_before_ms"] = doc["as_of_ms"] + 1000
        self.assertEqual(scored(doc)["ranked_synthetic_ids"], ["runtime_b"])

    def test_no_eligible_runtime_returns_no_route_not_score(self):
        doc = sample()
        for runtime in doc["runtimes"]:
            runtime["health"] = "OFFLINE"
        result = scored(doc)
        self.assertEqual(result["status"], "NO_ELIGIBLE_RUNTIME")
        self.assertEqual(result["ranked_synthetic_ids"], [])

    def test_missing_telemetry_blocks_all_partial_ranking(self):
        doc = sample()
        doc["telemetry"].pop()
        result = scored(doc)
        self.assertEqual(result["status"], "EVIDENCE_INCOMPLETE")
        self.assertEqual(result["rank_costs"], [])
        self.assertEqual(result["ranked_synthetic_ids"], [])

    def test_stale_telemetry_blocks_partial_ranking(self):
        doc = sample()
        doc["telemetry"][1]["sampled_at_ms"] -= 200_000
        result = scored(doc)
        self.assertEqual(result["status"], "EVIDENCE_INCOMPLETE")
        self.assertIn(["runtime_b", "telemetry_not_fresh"], result["evidence_blockers"])

    def test_future_telemetry_blocks_partial_ranking(self):
        doc = sample()
        doc["telemetry"][1]["sampled_at_ms"] += 100_000
        self.assertEqual(scored(doc)["status"], "EVIDENCE_INCOMPLETE")

    def test_insufficient_samples_does_not_fabricate_accuracy(self):
        doc = sample()
        doc["telemetry"][0]["sample_count"] = 1
        self.assertEqual(scored(doc)["status"], "EVIDENCE_INCOMPLETE")

    def test_rate_zero_fails_ranking(self):
        doc = sample()
        doc["telemetry"][0]["token_rate_p10"] = 0
        self.assertEqual(scored(doc)["status"], "EVIDENCE_INCOMPLETE")

    def test_quality_lower_bound_must_meet_floor(self):
        doc = sample()
        doc["telemetry"][1]["quality_lower_bound"] = .2
        value = scored(doc)
        self.assertEqual(value["status"], "EVIDENCE_INCOMPLETE")
        self.assertIn(["runtime_b", "quality_floor_unproven"], value["evidence_blockers"])

    def test_deadline_violation_fails_closed_even_if_other_node_good(self):
        doc = sample()
        doc["request"]["max_latency_ms"] = 11000
        self.assertEqual(scored(doc)["status"], "EVIDENCE_INCOMPLETE")

    def test_offline_power_required_fresh_for_all_eligible(self):
        doc = sample()
        doc["request"]["required_offline_s"] = 300
        doc["telemetry"][0]["battery_sampled_at_ms"] -= 100000
        result = scored(doc)
        self.assertEqual(result["status"], "EVIDENCE_INCOMPLETE")
        self.assertIn(["runtime_a", "power_evidence_not_fresh"], result["evidence_blockers"])

    def test_missing_battery_evidence_blocks_offline_coverage(self):
        doc = sample()
        doc["request"]["required_offline_s"] = 300
        power = doc["telemetry"][0]
        power["battery_remaining_wh"] = None
        power["dependency_watts"] = None
        power["battery_sampled_at_ms"] = None
        self.assertEqual(scored(doc)["status"], "EVIDENCE_INCOMPLETE")

    def test_insufficient_offline_survival_blocks_rank(self):
        doc = sample()
        doc["request"]["required_offline_s"] = 4000
        result = scored(doc)
        self.assertEqual(result["status"], "EVIDENCE_INCOMPLETE")

    def test_fresh_battery_evidence_can_rank_both_candidates(self):
        doc = sample()
        doc["request"]["required_offline_s"] = 900
        self.assertEqual(scored(doc)["status"], "RANKED_SYNTHETIC_ONLY")

    def test_battery_partial_fields_rejected(self):
        doc = sample()
        doc["telemetry"][0]["battery_remaining_wh"] = None
        with self.assertRaisesRegex(ValueError, "incomplete"):
            scored(doc)

    def test_zero_power_is_unusable_for_survival(self):
        doc = sample()
        doc["request"]["required_offline_s"] = 200
        doc["telemetry"][0]["dependency_watts"] = 0
        result = scored(doc)
        self.assertIn(["runtime_a", "power_denominator_unproven"], result["evidence_blockers"])

    def test_nan_and_negative_prohibited(self):
        for value in (float("nan"), -1, float("inf")):
            with self.subTest(value=value):
                doc = sample()
                doc["telemetry"][1]["watts_p95"] = value
                with self.assertRaises(ValueError):
                    scored(doc)

    def test_bool_does_not_count_as_int(self):
        doc = sample()
        doc["runtimes"][0]["free_slots"] = True
        with self.assertRaisesRegex(ValueError, "free_slots"):
            scored(doc)

    def test_lexical_tie_break_is_deterministic(self):
        doc = sample()
        doc["telemetry"][1]["ttft_p95_ms"] = 100
        doc["telemetry"][1]["token_rate_p10"] = 100
        self.assertEqual(scored(doc)["ranked_synthetic_ids"][0], "runtime_a")

    def test_telemetry_cannot_mint_eligibility_for_offline_node(self):
        doc = sample()
        doc["runtimes"][0]["health"] = "OFFLINE"
        doc["telemetry"][0]["ttft_p95_ms"] = 0
        doc["telemetry"][0]["token_rate_p10"] = 100000
        result = scored(doc)
        self.assertEqual(result["ranked_synthetic_ids"], ["runtime_b"])

    def test_client_route_claim_cannot_be_proven_by_own_hash(self):
        doc = sample()
        result = scored(doc)
        self.assertRegex(result["snapshot_digest"], r"^[0-9a-f]{64}$")
        self.assertFalse(result["physical_execution_attested"])
        self.assertFalse(result["authority_to_dispatch"])


if __name__ == "__main__":
    unittest.main()
