"""Offline synthetic crypto-contract tests; private keys exist in memory only."""
from __future__ import annotations
import copy
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import unittest
from research.lfm3_top5.prospective_holdout_gate import (
    CHALLENGES, CODES, DATA_SCHEMA, MODEL_SHA, sha
)
from research.lfm3_top5.independent_custody_handoff import (
    SCHEMA, KEY_SCHEMA, COMMIT_SCHEMA, FROZEN_PREREG_SHA,
    ROLES, role_payload, verify_handoff
)

def fictional_cases():
    cases=[]
    for i,cat in enumerate(CODES):
        for j in range(6):
            cases.append({
                "id": f"FICT{i:02d}{j:02d}",
                "note": ("Public fictional synthetic research note, not a real "
                         "system or host. E1 (verified record): entirely imagined "
                         f"category={cat} example {i}-{j} is assigned by a tester. "
                         "No network, person, service or secret was observed."),
                "expected":{"category":cat,"evidence":"E1"},
                "challenges":[CHALLENGES[(i+j)%len(CHALLENGES)]],
            })
    return {"schema":DATA_SCHEMA,"cases":cases}

def signed_synthetic():
    data=fictional_cases()
    keys={role:Ed25519PrivateKey.generate() for role in ROLES}
    registry={"schema":KEY_SCHEMA,"roles":{
        role:{"key_id":f"fictional.{role}.20261109",
              "ed25519_public_key_hex":keys[role].public_key().public_bytes(
                  encoding=serialization.Encoding.Raw,
                  format=serialization.PublicFormat.Raw).hex()}
        for role in ROLES}}
    commitment={"schema":COMMIT_SCHEMA,"dataset_sha256":sha(data),
                "prereg_sha256":FROZEN_PREREG_SHA,
                "checkpoint_sha256":MODEL_SHA}
    bundle={"schema":SCHEMA,"commitment":commitment,
            "claimed_external_anchor_reference":"synthetic:NOT-REAL-ANCHOR-20261109",
            "signatures":{}}
    for role in ROLES:
        bundle["signatures"][role]={
            "key_id":registry["roles"][role]["key_id"],
            "signature_hex":keys[role].sign(role_payload(
                role,commitment,bundle["signatures"],
                bundle["claimed_external_anchor_reference"])).hex()}
    return data,bundle,registry

class SignedHandoffContract(unittest.TestCase):
    def check_hold(self,data,package,roots,reason=None):
        d=verify_handoff(data,package,roots)
        self.assertEqual(d["decision"],"HOLD",d)
        for key in ("offline_signatures_valid","independent_author_verified",
                    "trusted_key_ownership_verified","external_anchor_verified",
                    "pre_inference_seal_verified","external_custody_verified",
                    "model_value_verified","production_authorized"):
            self.assertFalse(d[key],key)
        if reason is not None:
            self.assertEqual(d["reason"],reason)
        return d

    def test_valid_fake_bundle_is_not_independent_custody(self):
        data,pack,roots=signed_synthetic()
        res=verify_handoff(data,pack,roots)
        self.assertEqual(res["decision"],"PASS_OFFLINE_SIGNATURE_STRUCTURE_ONLY")
        self.assertEqual(res["separately_keyed_signature_count"],3)
        self.assertTrue(res["offline_signatures_valid"])
        for key in ("independent_author_verified","trusted_key_ownership_verified",
                    "external_anchor_verified","pre_inference_seal_verified",
                    "external_custody_verified","model_value_verified",
                    "production_authorized"):
            self.assertFalse(res[key])

    def test_note_tamper_rejected(self):
        data,p,r=signed_synthetic()
        data["cases"][0]["note"]+=" forged appendix."
        self.check_hold(data,p,r,"case_or_prereg_commitment_mismatch")

    def test_label_tamper_rejected(self):
        data,p,r=signed_synthetic()
        data["cases"][0]["expected"]["category"]=CODES[1]
        self.check_hold(data,p,r,"case_or_prereg_commitment_mismatch")

    def test_signature_tamper_all_roles_rejected(self):
        for role in ROLES:
            data,p,r=signed_synthetic()
            s=p["signatures"][role]["signature_hex"]
            p["signatures"][role]["signature_hex"]=("0" if s[0]!="0" else "1")+s[1:]
            self.check_hold(data,p,r)

    def test_role_swap_rejected(self):
        data,p,r=signed_synthetic()
        p["signatures"]["author"]["signature_hex"]=p["signatures"]["reviewer"]["signature_hex"]
        self.check_hold(data,p,r,"invalid_signature_author")

    def test_same_public_key_for_two_roles_holds(self):
        data,p,r=signed_synthetic()
        r["roles"]["reviewer"]["ed25519_public_key_hex"]=r["roles"]["author"]["ed25519_public_key_hex"]
        self.check_hold(data,p,r,"missing_nonoverlapping_three_role_key_roster")

    def test_same_key_id_for_two_roles_holds(self):
        data,p,r=signed_synthetic()
        r["roles"]["reviewer"]["key_id"]=r["roles"]["author"]["key_id"]
        self.check_hold(data,p,r,"missing_nonoverlapping_three_role_key_roster")

    def test_unauthorized_public_key_replaced_rejected(self):
        data,p,r=signed_synthetic()
        other=signed_synthetic()[2]
        r["roles"]["author"]["ed25519_public_key_hex"]=other["roles"]["author"]["ed25519_public_key_hex"]
        self.check_hold(data,p,r,"invalid_signature_author")

    def test_anchor_reference_mutation_rejected(self):
        data,p,r=signed_synthetic()
        p["claimed_external_anchor_reference"]="synthetic:DIFFERENT-NONREAL-ANCHOR"
        self.check_hold(data,p,r,"invalid_signature_witness")

    def test_anchor_reference_missing_holds(self):
        data,p,r=signed_synthetic()
        p["claimed_external_anchor_reference"]=""
        self.check_hold(data,p,r,"missing_external_anchor_reference")

    def test_witness_signature_missing_holds(self):
        data,p,r=signed_synthetic()
        del p["signatures"]["witness"]
        self.check_hold(data,p,r,"missing_signature_or_unexpected_role")

    def test_false_production_authority_is_rejected(self):
        data,p,r=signed_synthetic()
        p["production_authorized"]=True
        self.check_hold(data,p,r,"invalid_handoff_bundle")

    def test_unrecognized_role_and_extra_trust_key_hold(self):
        data,p,r=signed_synthetic()
        p["signatures"]["production"]=p["signatures"]["witness"]
        self.check_hold(data,p,r)
        data,p,r=signed_synthetic()
        r["roles"]["admin"]=r["roles"]["author"]
        self.check_hold(data,p,r)

    def test_wrong_prereg_and_checkpoint_holds(self):
        data,p,r=signed_synthetic()
        p["commitment"]["prereg_sha256"]="0"*64
        self.check_hold(data,p,r,"case_or_prereg_commitment_mismatch")
        data,p,r=signed_synthetic()
        p["commitment"]["checkpoint_sha256"]="0"*64
        self.check_hold(data,p,r,"case_or_prereg_commitment_mismatch")

    def test_duplicate_case_and_case_balance_holds(self):
        data,p,r=signed_synthetic()
        data["cases"][1]["id"]=data["cases"][0]["id"]
        self.check_hold(data,p,r,"invalid_or_unbalanced_independent_case_contract")
        data,p,r=signed_synthetic()
        data["cases"]=data["cases"][:30]
        self.check_hold(data,p,r,"invalid_or_unbalanced_independent_case_contract")

    def test_key_id_mismatch_and_invalid_signature_length(self):
        data,p,r=signed_synthetic()
        p["signatures"]["reviewer"]["key_id"]="not-those-keys-2026"
        self.check_hold(data,p,r,"untrusted_signature_record_reviewer")
        data,p,r=signed_synthetic()
        p["signatures"]["witness"]["signature_hex"]="ef"
        self.check_hold(data,p,r,"untrusted_signature_record_witness")

    def test_caller_created_three_keys_still_not_attested(self):
        data,p,r=signed_synthetic()
        p["claimed_external_anchor_reference"]="https://example.invalid/fictional-anchor-111"
        # Changing the reference invalidates the witness signature; fabricated
        # references cannot be laundered into a trusted external timestamp.
        self.check_hold(data,p,r,"invalid_signature_witness")

    def test_replay_signed_bundle_from_another_dataset_holds(self):
        data,p,r=signed_synthetic()
        another=fictional_cases()
        another["cases"][-1]["note"]+=" changed"
        self.check_hold(another,p,r,"case_or_prereg_commitment_mismatch")

    def test_no_data_roster_or_bundle_holds(self):
        d,p,r=signed_synthetic()
        for bad in (None,{},[],True,"nonsense"):
            self.check_hold(bad,p,r)
            self.check_hold(d,bad,r)
            self.check_hold(d,p,bad)

if __name__=="__main__":
    unittest.main()
