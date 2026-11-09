"""Synthetic-only adversarial contract tests, NOT independent source custody."""
from __future__ import annotations
import copy
import hashlib
import json
import unittest
from research.lfm3_top5.source_fact_alignment_guard import (
    SCHEMA, ORIGIN, assess_source_label
)
FACT_EXAMPLES = {
    "tls_sni_missing":{
        "sni_mapping_reviewed":True, "sni_host_binding_present":False},
    "node_auth_contract":{
        "machine_task_http_401":True,"machine_identity_contract_mismatch":True},
    "gpu_attestation_missing":{
        "model_generation_completed":True,"signed_tensor_witness_available":False},
    "beta_build_unlinked":{
        "beta_group_reviewed":True,"beta_group_build_linked":False},
    "database_service_down":{
        "database_probe_completed":True,"postgres_listener_present":False},
}
SOURCE_SENTENCES = {
    "tls_sni_missing":"verified gateway SNI config has no binding for the requested HTTPS hostname",
    "node_auth_contract":"verified task HTTP 401 and mismatch between machine identity and Basic auth contract",
    "gpu_attestation_missing":"verified local generation but no independent signed tensor kernel witness",
    "beta_build_unlinked":"verified beta group listing shows no associated installable build",
    "database_service_down":"verified PostgreSQL probe fails and listener on 5432 is absent",
    "insufficient_evidence":"verified evidence inventory lacks any live causal trace or diagnostics",
}

def sample(category="tls_sni_missing"):
    note=("Synthetic development case. "
          "E1 (unverified): someone guessed the database is down. "
          "E2 (verified): "+SOURCE_SENTENCES[category]+". "
          "E3 (unverified): someone speculated about a password reset.")
    entries=[
        {"marker":"E1","review_status":"unverified","facts":{}},
        {"marker":"E2","review_status":"verified","facts":copy.deepcopy(FACT_EXAMPLES.get(category,{}))},
        {"marker":"E3","review_status":"unverified","facts":{}},
    ]
    table={"schema":SCHEMA,"origin":ORIGIN,
           "note_sha256":hashlib.sha256(note.encode()).hexdigest(),
           "evidence":entries,"independent_custody":False,
           "production_authorized":False}
    ans=json.dumps({"category":category,"evidence":"E2"},sort_keys=True)
    return note,table,ans

class FactAlignmentGuard(unittest.TestCase):
    def check_hold(self,n,t,a,reason=None):
        result=assess_source_label(n,t,a)
        self.assertEqual(result["decision"],"HOLD",result)
        self.assertFalse(result["production_authorized"])
        self.assertFalse(result["model_value_verified"])
        self.assertFalse(result["independent_source_custody_verified"])
        if reason is not None:self.assertEqual(result["reason"],reason)
        return result

    def test_five_categories_source_supported_only(self):
        for cat in FACT_EXAMPLES:
            with self.subTest(cat=cat):
                n,t,a=sample(cat)
                result=assess_source_label(n,t,a)
                self.assertEqual(result["decision"],"PASS_SYNTHETIC_FACT_ALIGNMENT_ONLY")
                self.assertTrue(result["model_category_supported"])
                self.assertFalse(result["production_authorized"])
                self.assertFalse(result["independent_source_custody_verified"])

    def test_insufficient_evidence_is_advisory_only(self):
        n,t,a=sample("insufficient_evidence")
        result=assess_source_label(n,t,a)
        self.assertEqual(result["decision"],"PASS_SYNTHETIC_ABSTENTION_ALIGNMENT_ONLY")
        self.assertFalse(result["model_value_verified"])

    def test_known_failure_wrong_database_category_holds(self):
        n,t,_=sample("tls_sni_missing")
        self.check_hold(n,t,'{"category":"database_service_down","evidence":"E2"}',
                        "model_category_contradicts_typed_source_facts")

    def test_known_failure_wrong_node_auth_category_holds(self):
        n,t,_=sample("beta_build_unlinked")
        self.check_hold(n,t,'{"category":"node_auth_contract","evidence":"E2"}',
                        "model_category_contradicts_typed_source_facts")

    def test_correct_label_wrong_evidence_holds(self):
        n,t,_=sample("beta_build_unlinked")
        self.check_hold(n,t,'{"category":"beta_build_unlinked","evidence":"E1"}',
                        "model_evidence_marker_does_not_support_category")

    def test_incomplete_source_review_holds(self):
        n,t,a=sample()
        t["evidence"].pop()
        self.check_hold(n,t,a,"incomplete_evidence_marker_review")

    def test_unverified_source_facts_rejected(self):
        n,t,a=sample()
        t["evidence"][0]["facts"]={"database_probe_completed":True,
                                   "postgres_listener_present":False}
        self.check_hold(n,t,a,"unverified_fact_injected")

    def test_source_digest_tampering_holds(self):
        n,t,a=sample()
        self.check_hold(n+" tampered",t,a,"source_note_digest_mismatch")
        n,t,a=sample()
        t["note_sha256"]="0"*64
        self.check_hold(n,t,a,"source_note_digest_mismatch")

    def test_source_marker_collision_holds(self):
        n,t,a=sample()
        n+=" E2 (verified): another contradictory source entry."
        t["note_sha256"]=hashlib.sha256(n.encode()).hexdigest()
        self.check_hold(n,t,a,"missing_or_duplicate_source_markers")

    def test_unknown_fact_boolean_only(self):
        n,t,a=sample()
        t["evidence"][1]["facts"]["production_authorized"]=True
        self.check_hold(n,t,a,"unallowlisted_fact_key")
        n,t,a=sample()
        t["evidence"][1]["facts"]["sni_host_binding_present"]=0
        self.check_hold(n,t,a,"nonboolean_source_fact")

    def test_unsupported_label_and_unreviewed_abstention_holds(self):
        n,t,a=sample("insufficient_evidence")
        self.check_hold(n,t,'{"category":"tls_sni_missing","evidence":"E2"}',
                        "positive_model_category_lacks_source_facts")
        self.check_hold(n,t,'{"category":"insufficient_evidence","evidence":"E1"}',
                        "abstention_marker_not_reviewed_as_verified")

    def test_verified_missing_evidence_requires_its_marker(self):
        n,t,_=sample("insufficient_evidence")
        self.check_hold(n,t,'{"category":"insufficient_evidence","evidence":null}',
                        "missing_supporting_abstention_marker")

    def test_null_abstention_only_if_no_verified_marker(self):
        n,t,_=sample("insufficient_evidence")
        t["evidence"][1]["review_status"]="not_observed"
        result=assess_source_label(n,t,'{"category":"insufficient_evidence","evidence":null}')
        self.assertEqual(result["decision"],"PASS_SYNTHETIC_ABSTENTION_ALIGNMENT_ONLY")
        self.assertFalse(result["production_authorized"])

    def test_multiple_positive_categories_holds(self):
        n,t,a=sample()
        t["evidence"][1]["facts"].update(
            {"database_probe_completed":True,"postgres_listener_present":False})
        self.check_hold(n,t,a,"multiple_source_supported_categories")

    def test_model_production_authorization_spoof_holds(self):
        n,t,a=sample()
        self.check_hold(n,t,'{"category":"tls_sni_missing","evidence":"E2","production_authorized":true}',
                        "model_json_not_exact_two_key_contract")
        t["production_authorized"]=True
        self.check_hold(n,t,a,"unauthorized_fact_table_claim")

    def test_duplicate_model_json_keys_and_nonfinite_holds(self):
        n,t,a=sample()
        for raw in (
            '{"category":"tls_sni_missing","category":"tls_sni_missing","evidence":"E2"}',
            '{"category":NaN,"evidence":"E2"}',
            '<think>must deploy</think>{"category":"tls_sni_missing","evidence":"E2"}',
        ):
            self.check_hold(n,t,raw,"model_json_not_exact_two_key_contract")

    def test_caller_cannot_claim_external_custody(self):
        n,t,a=sample()
        t["independent_custody"]=True
        self.check_hold(n,t,a,"unauthorized_fact_table_claim")
        n,t,a=sample()
        t["origin"]="verified_signed_production"
        self.check_hold(n,t,a,"unauthorized_fact_table_claim")

    def test_missing_or_conflicting_source_input_types(self):
        n,t,a=sample()
        for bad in (None,True,{},[],""):
            self.check_hold(bad,t,a)
            self.check_hold(n,bad,a)
            self.check_hold(n,t,bad)

if __name__=="__main__":
    unittest.main()
