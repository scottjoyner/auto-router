"""Synthetic-only full-cohort repeatability tests, no real model receipts."""
from __future__ import annotations
import copy
import hashlib
import json
import unittest
from research.lfm3_top5.prospective_holdout_gate import CODES, CHALLENGES, DATA_SCHEMA, MODEL_SHA, sha
from research.lfm3_top5.session_stability_gate import SCHEMA, STAGE_ORDER, audit_repeatability

def fixture():
    cases=[]
    for ci, code in enumerate(CODES):
        for j in range(6):
            cid=f"CASE{ci:02d}{j:02d}"
            note=(f"Synthetic independent-looking but entirely fabricated case {ci}-{j}; "
                  f"E1 (verified): the fictional operator records {code} with mock evidence. "
                  "No actual infrastructure, human incident or credential is represented.")
            cases.append({"id":cid,"note":note,
                          "expected":{"category":code,"evidence":"E1"},
                          "challenges":[CHALLENGES[(ci+j)%6]]})
    dataset={"schema":DATA_SCHEMA,"cases":cases}
    stages=[]
    for i,mode in enumerate(STAGE_ORDER):
        reqs=[]
        for j,item in enumerate(cases):
            reply=json.dumps(item["expected"],sort_keys=True)
            reqs.append({
                "case_id":item["id"],"source_note_sha256":sha(item["note"]),
                "response":reply,
                "response_sha256":hashlib.sha256(reply.encode()).hexdigest(),
                "request_id":f"synthetic-stage-{i}-request-{j:03d}",
                "finish_reason":"stop","http_plus_validation_ms":1200.0+j,
            })
        stages.append({
            "index":i,"mode":mode,"session_id":f"synthetic-session-{i}",
            "start_monotonic_ns":int((i+1)*2e12),
            "end_monotonic_ns":int((i+1)*2e12+int(1e12)),
            "device":"none" if mode=="cpu" else "Vulkan0",
            "offloaded_layers_requested":0 if mode=="cpu" else 8,
            "capacity_gate_claimed_pass":True,"child_stopped":True,
            "temporary_port_closed":True,"temporary_log_removed":True,
            "requests":reqs
        })
    return dataset,sha(dataset),{
        "schema":SCHEMA,"dataset_sha256":sha(dataset),
        "checkpoint_sha256":MODEL_SHA,"stages":stages,
        "production_authorized":False,"independent_gpu_attested":False,
    }

class CohortStabilityContract(unittest.TestCase):
    def hold(self,d,s,b):
        out=audit_repeatability(d,s,b)
        self.assertEqual(out["decision"],"HOLD",out)
        self.assertFalse(out["production_authorized"])
        return out

    def test_all_cases_in_four_distinct_stages(self):
        d,s,b=fixture()
        out=audit_repeatability(d,s,b)
        self.assertEqual(out["decision"],"PASS_OFFLINE_PAIRING_CONTRACT_ONLY",out)
        self.assertEqual(out["unique_case_count"],36)
        self.assertEqual(out["raw_request_count"],144)
        self.assertEqual(out["session_count"],4)
        for mode in ("cpu","vulkan8"):
            self.assertEqual(out["modes"][mode]["correct_both_sessions"],36)
            self.assertEqual(out["modes"][mode]["attempts"],72)
            self.assertEqual(out["modes"][mode]["correctness_flip_cases"],0)
        for k in ("independent_repeatability_verified","external_custody_verified",
                  "accelerator_attested","model_value_verified","production_authorized"):
            self.assertFalse(out[k])

    def test_second_session_wrong_answer_exposes_case_flip(self):
        d,s,b=fixture()
        row=b["stages"][3]["requests"][0]
        row["response"]='{"category":"insufficient_evidence","evidence":"E1"}'
        row["response_sha256"]=hashlib.sha256(row["response"].encode()).hexdigest()
        out=audit_repeatability(d,s,b)
        self.assertEqual(out["modes"]["cpu"]["correct_both_sessions"],35)
        self.assertEqual(out["modes"]["cpu"]["correctness_flip_cases"],1)
        self.assertEqual(out["unique_case_count"],36)

    def test_identical_wrong_answer_not_treated_as_correct(self):
        d,s,b=fixture()
        for stage in (b["stages"][0],b["stages"][3]):
            row=stage["requests"][0]
            row["response"]='{"category":"insufficient_evidence","evidence":"E1"}'
            row["response_sha256"]=hashlib.sha256(row["response"].encode()).hexdigest()
        out=audit_repeatability(d,s,b)
        self.assertEqual(out["modes"]["cpu"]["identical_but_wrong_answers"],1)
        self.assertEqual(out["modes"]["cpu"]["correct_both_sessions"],35)

    def test_fake_release_authority_is_counted_not_obeyed(self):
        d,s,b=fixture()
        row=b["stages"][1]["requests"][0]
        row["response"]='{"category":"tls_sni_missing","evidence":"E1","production_authorized":true}'
        row["response_sha256"]=hashlib.sha256(row["response"].encode()).hexdigest()
        out=audit_repeatability(d,s,b)
        self.assertEqual(out["modes"]["vulkan8"]["unsafe_authority_claim_attempts"],1)
        self.assertFalse(out["production_authorized"])

    def test_missing_one_case_in_second_stage_holds(self):
        d,s,b=fixture()
        b["stages"][2]["requests"].pop()
        self.hold(d,s,b)

    def test_duplicated_case_in_stage_holds(self):
        d,s,b=fixture()
        b["stages"][2]["requests"][1]["case_id"]=b["stages"][2]["requests"][0]["case_id"]
        self.hold(d,s,b)

    def test_request_reuse_across_sessions_holds(self):
        d,s,b=fixture()
        b["stages"][3]["requests"][0]["request_id"]=b["stages"][0]["requests"][0]["request_id"]
        self.hold(d,s,b)

    def test_same_session_id_spoof_holds(self):
        d,s,b=fixture()
        b["stages"][3]["session_id"]=b["stages"][0]["session_id"]
        self.hold(d,s,b)

    def test_bad_monotonic_time_holds(self):
        d,s,b=fixture()
        b["stages"][1]["start_monotonic_ns"]=b["stages"][0]["end_monotonic_ns"]
        self.hold(d,s,b)
        d,s,b=fixture()
        b["stages"][1]["end_monotonic_ns"]=True
        self.hold(d,s,b)

    def test_stage_order_or_gpu_layers_holds(self):
        d,s,b=fixture()
        b["stages"][0]["mode"]="vulkan8"
        self.hold(d,s,b)
        d,s,b=fixture()
        b["stages"][1]["offloaded_layers_requested"]=0
        self.hold(d,s,b)

    def test_tampered_note_or_response_holds(self):
        d,s,b=fixture()
        d["cases"][0]["note"]+=" altered"
        self.hold(d,s,b)
        d,s,b=fixture()
        b["stages"][0]["requests"][0]["response"]+="."
        self.hold(d,s,b)

    def test_missing_safety_or_cleanup_claim_holds(self):
        for key in ("capacity_gate_claimed_pass","temporary_port_closed",
                    "temporary_log_removed","child_stopped"):
            d,s,b=fixture()
            b["stages"][0][key]=False
            self.hold(d,s,b)

    def test_untrusted_latency_and_type_holds(self):
        for value in (True,float("nan"),float("inf"),0.0,-1.0):
            d,s,b=fixture()
            b["stages"][0]["requests"][0]["http_plus_validation_ms"]=value
            self.hold(d,s,b)
        for bad in (None,False,[],{},"bad"):
            d,s,b=fixture()
            self.hold(bad,s,b)
            self.hold(d,bad,b)

    def test_caller_supplied_seal_only_is_not_independent(self):
        d,s,b=fixture()
        out=audit_repeatability(d,s,b)
        self.assertFalse(out["external_custody_verified"])
        self.assertFalse(out["independent_repeatability_verified"])
        self.hold(d,"a"*64,b)

if __name__=="__main__":
    unittest.main()
