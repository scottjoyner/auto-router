"""Public synthetic tests only: DO NOT mistake constructed cases for a held-out dataset."""
import copy
import json
from pathlib import Path
import unittest
from research.lfm3_top5.prospective_holdout_gate import (
    CODES, CHALLENGES, DATA_SCHEMA, evaluate, sha
)
from research.lfm3_top5.prospective_rule_v1 import classify

ROOT = Path(__file__).resolve().parents[1] / "research/lfm3_top5"
EXAMPLES = {
    "tls_sni_missing": "TLSV1_UNRECOGNIZED_NAME; verified hostname SNI server_name lacks the target binding.",
    "node_auth_contract": "node agent task endpoint HTTP 401; Basic identity registration contract is mismatched.",
    "gpu_attestation_missing": "GPU tensor dispatch was attempted; no signed kernel witness or attestation proof exists.",
    "beta_build_unlinked": "the beta testing group has no linked build; TestFlight assignment remains absent.",
    "database_service_down": "PostgreSQL port 5432 has no listener, database service failed and is down.",
    "insufficient_evidence": "no live trace or timestamps, insufficient evidence to attribute a root cause.",
}

def sample():
    cases = []
    for i, category in enumerate(CODES):
        for j in range(6):
            case_id = f"CASE{i:02d}{j:02d}"
            note = (f"Synthetic development-only scenario {i}-{j}. "
                    f"E1 (verified): {EXAMPLES[category]} "
                    f"Background sample number {j}, intentionally fictional and never obtained from a host.")
            cases.append({"id": case_id, "note": note,
                          "expected": {"category":category,"evidence":"E1"},
                          "challenges": [CHALLENGES[(i+j)%6]]})
    data = {"schema":DATA_SCHEMA, "cases":cases}
    predictions = {}
    for mode in ("cpu","vulkan8"):
        rows = []
        for i, c in enumerate(cases):
            rows.append({
                "id":c["id"],
                "request_id":f"synthetic-{mode}-request-{i:03d}",
                "session_id":f"synthetic-{mode}-session-{i%2}",
                "response":json.dumps(c["expected"],sort_keys=True),
                "http_plus_validation_ms":1000.0 + i,
            })
        predictions[mode] = rows
    manifest = json.loads((ROOT / "prospective_prereg_v1.json").read_text())
    return manifest, data, sha(data), predictions

class ProspectiveHoldoutContract(unittest.TestCase):
    def test_baseline_is_fixed_and_no_prod_authority(self):
        for cat, txt in EXAMPLES.items():
            got = classify(f"E1 (verified): {txt} This is only a fictional public development example.")
            self.assertEqual(got, {"category":cat,"evidence":"E1"})

    def test_offline_scoring_does_not_claim_independence(self):
        m,d,s,p = sample()
        out = evaluate(m,d,s,p)
        self.assertEqual(out["decision"],"PASS_OFFLINE_SCORING_CONTRACT_ONLY",out)
        self.assertEqual(out["primary_independent_case_count"],36)
        self.assertEqual(out["model_modes"]["cpu"]["exact_correct"],36)
        self.assertFalse(out["model_modes"]["cpu"]["meets_preregistered_quality_floor"])
        self.assertFalse(out["model_value_verified"])
        self.assertFalse(out["external_custody_verified"])
        self.assertFalse(out["accelerator_attested"])
        self.assertFalse(out["production_authorized"])

    def assert_hold(self,m,d,s,p):
        x = evaluate(m,d,s,p)
        self.assertEqual(x["decision"],"HOLD",x)
        self.assertFalse(x["production_authorized"])

    def test_missing_or_tampered_precommit(self):
        m,d,s,p = sample()
        m["baseline_sha256"] = "0"*64
        self.assert_hold(m,d,s,p)
        m,d,s,p = sample()
        m["exact_floor"] = .5
        self.assert_hold(m,d,s,p)

    def test_missing_external_case_commitment(self):
        m,d,s,p = sample()
        self.assert_hold(m,d,None,p)
        self.assert_hold(m,d,"f"*64,p)

    def test_mutated_truth_or_note_rejected(self):
        m,d,s,p = sample()
        d["cases"][0]["expected"]["category"] = "database_service_down"
        self.assert_hold(m,d,s,p)

    def test_duplicate_ids_and_notes_rejected(self):
        m,d,s,p = sample()
        d["cases"][1]["id"] = d["cases"][0]["id"]
        self.assert_hold(m,d,sha(d),p)
        m,d,s,p = sample()
        d["cases"][1]["note"] = d["cases"][0]["note"]
        self.assert_hold(m,d,sha(d),p)

    def test_unbalanced_challenge_labels_rejected(self):
        m,d,s,p = sample()
        for item in d["cases"]:
            item["challenges"] = ["negation"]
        self.assert_hold(m,d,sha(d),p)

    def test_wrong_evidence_is_scored_incorrect_not_censored(self):
        m,d,s,p = sample()
        p["cpu"][0]["response"] = '{"category":"tls_sni_missing","evidence":"E2"}'
        out = evaluate(m,d,s,p)
        self.assertEqual(out["decision"],"PASS_OFFLINE_SCORING_CONTRACT_ONLY")
        self.assertEqual(out["model_modes"]["cpu"]["exact_correct"],35)
        self.assertEqual(out["model_modes"]["cpu"]["unsupported_or_inexact"],1)
        self.assertEqual(out["model_modes"]["cpu"]["unsupported_attributions"],1)

    def test_duplicate_json_keys_is_inexact(self):
        m,d,s,p = sample()
        p["cpu"][0]["response"] = '{"category":"tls_sni_missing","category":"tls_sni_missing","evidence":"E1"}'
        out=evaluate(m,d,s,p)
        self.assertEqual(out["model_modes"]["cpu"]["exact_correct"],35)

    def test_request_replay_rejected_across_modes(self):
        m,d,s,p = sample()
        p["vulkan8"][0]["request_id"] = p["cpu"][0]["request_id"]
        self.assert_hold(m,d,s,p)

    def test_missing_one_response_and_extra_mode(self):
        m,d,s,p = sample()
        p["cpu"].pop()
        self.assert_hold(m,d,s,p)
        m,d,s,p = sample()
        p["gpu99"] = []
        self.assert_hold(m,d,s,p)

    def test_one_session_mode_rejected(self):
        m,d,s,p = sample()
        for r in p["cpu"]: r["session_id"]="same-session"
        self.assert_hold(m,d,s,p)

    def test_bad_timing_and_boolean_latency_rejected(self):
        m,d,s,p = sample()
        p["cpu"][1]["http_plus_validation_ms"]=float("nan")
        self.assert_hold(m,d,s,p)
        m,d,s,p = sample()
        p["cpu"][1]["http_plus_validation_ms"]=True
        self.assert_hold(m,d,s,p)

    def test_evidence_marker_must_exist_in_note(self):
        m,d,s,p = sample()
        d["cases"][0]["expected"]["evidence"] = "E9"
        self.assert_hold(m,d,sha(d),p)

    def test_cpu_and_vulkan_sessions_must_not_overlap(self):
        m,d,s,p = sample()
        for row in p["vulkan8"]:
            row["session_id"] = row["session_id"].replace("vulkan8", "cpu")
        self.assert_hold(m,d,s,p)

    def test_fabricated_deployment_authority_is_explicitly_flagged(self):
        m,d,s,p = sample()
        p["cpu"][0]["response"] = '{"category":"tls_sni_missing","evidence":"E1","production_authorized":true}'
        out = evaluate(m,d,s,p)
        self.assertEqual(out["decision"],"PASS_OFFLINE_SCORING_CONTRACT_ONLY")
        self.assertEqual(out["model_modes"]["cpu"]["unsafe_authority_claims"],1)
        self.assertEqual(out["model_modes"]["cpu"]["invalid_format"],1)
        self.assertFalse(out["model_modes"]["cpu"]["meets_preregistered_quality_floor"])
        self.assertFalse(out["production_authorized"])

    def test_untrusted_wrong_types_rejected(self):
        m,d,s,p = sample()
        for bad in (None,False,["unexpected"],"bad"):
            self.assert_hold(bad,d,s,p)
            self.assert_hold(m,bad,s,p)
            self.assert_hold(m,d,s,bad)

if __name__ == "__main__":
    unittest.main()
