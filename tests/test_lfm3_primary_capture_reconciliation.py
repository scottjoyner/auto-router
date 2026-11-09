"""Cross-contract selection tests; fabricated cases and ephemeral keys only."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

from research.lfm3_top5.primary_capture_reconciliation import (
    SELECTOR, reconcile_primary
)
from research.lfm3_top5.prospective_holdout_gate import sha
from tests.test_lfm3_independent_custody_handoff import signed_synthetic
from tests.test_lfm3_session_stability_gate import fixture

ROOT = Path(__file__).resolve().parents[1] / "research/lfm3_top5"

def package():
    cases,bundle,roster=signed_synthetic()
    _other_cases,_other_sha,capture=fixture()
    capture["dataset_sha256"]=sha(cases)
    for stage in capture["stages"]:
        for src,dst in zip(cases["cases"],stage["requests"]):
            dst["case_id"]=src["id"]
            dst["source_note_sha256"]=sha(src["note"])
            answer=json.dumps(src["expected"],sort_keys=True)
            dst["response"]=answer
            dst["response_sha256"]=hashlib.sha256(answer.encode()).hexdigest()
    prereg=json.loads((ROOT/"prospective_prereg_v1.json").read_text())
    return prereg,cases,bundle,roster,capture

def change_answer(stage,rank,reply):
    row=stage["requests"][rank]
    row["response"]=reply
    row["response_sha256"]=hashlib.sha256(reply.encode()).hexdigest()

class PrimaryCaptureReconciliation(unittest.TestCase):
    def run_case(self,pack):
        return reconcile_primary(*pack)

    def assert_hold(self,pack,prefix=None):
        out=self.run_case(pack)
        self.assertEqual(out["decision"],"HOLD",out)
        self.assertFalse(out["production_authorized"])
        self.assertFalse(out["external_custody_verified"])
        self.assertFalse(out["model_value_verified"])
        if prefix:
            self.assertTrue(out["reason"].startswith(prefix),out["reason"])
        return out

    def test_signed_synthetic_complete_contract(self):
        out=self.run_case(package())
        self.assertEqual(out["decision"],"PASS_OFFLINE_RECONCILIATION_CONTRACT_ONLY",out)
        self.assertEqual(out["unique_case_count"],36)
        self.assertEqual(out["primary_answer_rows"],72)
        self.assertEqual(out["all_stage_answer_rows"],144)
        self.assertEqual(out["selector"],SELECTOR)
        self.assertTrue(out["primary_case_selection_verified"])
        self.assertTrue(out["offline_signature_structure_verified"])
        self.assertTrue(out["scoring_contract_verified"])
        for mode in ("cpu","vulkan8"):
            self.assertEqual(out["mode_scores"][mode]["exact_correct"],36)
            self.assertEqual(out["repeatability"][mode]["correct_both_sessions"],36)
            self.assertTrue(out["quality_development_screen"][mode]["advisory_no_regression_signal"])
            self.assertFalse(out["quality_development_screen"][mode]["independent_quality_verified"])
        for key in ("independent_author_verified","trusted_key_ownership_verified",
                    "external_anchor_verified","pre_inference_seal_verified",
                    "external_custody_verified","model_value_verified",
                    "accelerator_attested","production_authorized"):
            self.assertFalse(out[key])

    def test_different_request_order_does_not_change_primary_selection(self):
        a=package()
        out_a=self.run_case(a)
        b=package()
        for stage in b[4]["stages"]:
            stage["requests"].reverse()
        out_b=self.run_case(b)
        self.assertEqual(out_a["selection_manifest_sha256"],
                         out_b["selection_manifest_sha256"])
        self.assertEqual(out_a["mode_scores"],out_b["mode_scores"])

    def test_not_selecting_best_response_after_seeing_scores(self):
        pack=package()
        # Odd lexical case rank picks second CPU session; first CPU session is
        # intentionally wrong but the primary result must still use second.
        change_answer(pack[4]["stages"][0],1,
                      '{"category":"insufficient_evidence","evidence":"E1"}')
        out=self.run_case(pack)
        self.assertEqual(out["mode_scores"]["cpu"]["exact_correct"],36)
        self.assertEqual(out["repeatability"]["cpu"]["first_correct"],35)
        self.assertEqual(out["repeatability"]["cpu"]["correctness_flip_cases"],1)
        self.assertFalse(out["quality_development_screen"]["cpu"]["advisory_no_regression_signal"])

    def test_wrong_selected_response_is_not_silently_swapped(self):
        pack=package()
        # Even lexical rank selects first session; second correct cannot
        # replace the wrong primary output.
        change_answer(pack[4]["stages"][0],0,
                      '{"category":"insufficient_evidence","evidence":"E1"}')
        out=self.run_case(pack)
        self.assertEqual(out["mode_scores"]["cpu"]["exact_correct"],35)
        self.assertEqual(out["repeatability"]["cpu"]["correctness_flip_cases"],1)
        self.assertFalse(out["quality_development_screen"]["cpu"]["advisory_no_regression_signal"])

    def test_all_stages_unsafe_claim_in_unselected_repeat_is_flagged(self):
        pack=package()
        change_answer(pack[4]["stages"][3],0,
                      '{"category":"tls_sni_missing","evidence":"E1","production_authorized":true}')
        out=self.run_case(pack)
        self.assertEqual(out["decision"],"PASS_OFFLINE_RECONCILIATION_CONTRACT_ONLY")
        self.assertEqual(out["mode_scores"]["cpu"]["unsafe_authority_claims"],0)
        self.assertEqual(out["repeatability"]["cpu"]["unsafe_authority_claim_attempts"],1)
        self.assertFalse(out["quality_development_screen"]["cpu"]["advisory_no_regression_signal"])
        self.assertFalse(out["production_authorized"])

    def test_wrong_all_stages_still_structural_only(self):
        pack=package()
        reply='{"category":"insufficient_evidence","evidence":null}'
        for stage in pack[4]["stages"]:
            for i in range(len(stage["requests"])):
                change_answer(stage,i,reply)
        out=self.run_case(pack)
        self.assertEqual(out["decision"],"PASS_OFFLINE_RECONCILIATION_CONTRACT_ONLY")
        self.assertFalse(out["quality_development_screen"]["cpu"]["primary_meets_frozen_quality_floor"])
        self.assertFalse(out["quality_development_screen"]["vulkan8"]["advisory_no_regression_signal"])
        self.assertFalse(out["model_value_verified"])

    def test_invalid_or_missing_authenticity_material_blocks_everything(self):
        p=package();p=list(p);p[3]=None
        self.assert_hold(p,"handoff_")
        p=package();p=list(p)
        p[2]["signatures"]["witness"]["signature_hex"]="0"*128
        self.assert_hold(p,"handoff_")
        p=package();p=list(p)
        p[3]["roles"]["witness"]["ed25519_public_key_hex"]=(
            p[3]["roles"]["author"]["ed25519_public_key_hex"])
        self.assert_hold(p,"handoff_")

    def test_corrupt_case_or_oracle_and_prereg_rejected(self):
        p=list(package())
        p[1]["cases"][0]["expected"]["evidence"]="E2"
        self.assert_hold(p,"handoff_")
        p=list(package())
        p[0]["exact_floor"]=0.0
        self.assert_hold(p,"scorer_")

    def test_missing_stage_or_replay_request_id_rejected(self):
        p=list(package())
        p[4]["stages"].pop()
        self.assert_hold(p,"repeatability_")
        p=list(package())
        p[4]["stages"][2]["requests"][0]["request_id"]=(
            p[4]["stages"][1]["requests"][0]["request_id"])
        self.assert_hold(p,"repeatability_")

    def test_cleanup_failure_or_stage_order_rejected(self):
        p=list(package())
        p[4]["stages"][2]["child_stopped"]=False
        self.assert_hold(p,"repeatability_")
        p=list(package())
        p[4]["stages"][1]["mode"]="cpu"
        self.assert_hold(p,"repeatability_")

    def test_nonfinite_time_or_tampered_response_hash_blocks(self):
        p=list(package())
        p[4]["stages"][0]["requests"][0]["http_plus_validation_ms"]=True
        self.assert_hold(p,"repeatability_")
        p=list(package())
        p[4]["stages"][0]["requests"][0]["response_sha256"]="0"*64
        self.assert_hold(p,"repeatability_")

    def test_missing_inputs_do_not_authorize_release(self):
        for index in range(5):
            p=list(package())
            p[index]=None
            self.assert_hold(p)

    def test_cli_real_subprocess_structural_pass_and_redaction(self):
        pack=package()
        with tempfile.TemporaryDirectory(prefix="lfm3-reconcile-test-") as directory:
            names=("manifest","dataset","bundle","key-roster","capture")
            for name,value in zip(names,pack):
                (Path(directory)/(name+".json")).write_text(
                    json.dumps(value,sort_keys=True))
            cmd=[sys.executable,"-m",
                 "research.lfm3_top5.primary_capture_reconciliation"]
            for name in names:
                cmd.extend(["--"+name,str(Path(directory)/(name+".json"))])
            res=subprocess.run(cmd,text=True,capture_output=True,timeout=20)
            self.assertEqual(res.returncode,0,res.stderr)
            out=json.loads(res.stdout)
            self.assertEqual(out["decision"],"PASS_OFFLINE_RECONCILIATION_CONTRACT_ONLY")
            self.assertFalse(out["production_authorized"])
            self.assertNotIn(pack[1]["cases"][0]["note"],res.stdout)
            pack[4]["stages"][0]["requests"][0]["response"]="tampered"
            (Path(directory)/"capture.json").write_text(json.dumps(pack[4],sort_keys=True))
            bad=subprocess.run(cmd,text=True,capture_output=True,timeout=20)
            self.assertEqual(bad.returncode,2,bad.stderr)
            self.assertEqual(json.loads(bad.stdout)["decision"],"HOLD")
            (Path(directory)/"bundle.json").unlink()
            missing=subprocess.run(cmd,text=True,capture_output=True,timeout=20)
            self.assertEqual(missing.returncode,2)
            self.assertEqual(json.loads(missing.stdout)["decision"],"HOLD")

if __name__=="__main__":
    unittest.main()
