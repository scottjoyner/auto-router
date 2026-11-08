"""Synthetic-only fail-closed tests for fixed LFM3 narrative triage research.

Fixtures are invented narrative evidence with manually specified reference
labels. No live host telemetry, credentials, process IDs or unsigned
runtime receipts are published in this test file.
"""
import copy,hashlib,json,random,unittest
from pathlib import Path
from research.lfm3_top5.unstructured_triage_audit import (
  MODEL_SHA,SCRIPT_SHA,FIXTURE_SHA,SCENARIOS,SOURCE_CASE_ORDER,ORDER,
  _fixture_ok,canonical,exact_answer,audit_unstructured
)

FIXTURE=Path(__file__).resolve().parents[1]/"research/lfm3_top5/unstructured_public_fixture.json"

def fixture():
    return json.loads(FIXTURE.read_text())

def sha(x):
    return hashlib.sha256(x.encode()).hexdigest()

def bundle():
    f=fixture()
    manifest={
      "schema":"lfm3.deathstar.unstructured-triage-preregistered.v1",
      "host":"deathstar-XPS-8920","study_date":"2026-11-08",
      "primary_order":[{"mode":m,"block":b} for m,b in ORDER],
      "task_ids":list(SCENARIOS),
      "script_sha256":SCRIPT_SHA,"fixture_sha256":FIXTURE_SHA,
      "checkpoint_sha256":MODEL_SHA,
      "exploratory_cpu_block_excluded":True,"maximum_requests":24,
      "independent_gpu_execution_attestation":False,
      "production_authorized":False
    }
    cap={
      "MemAvailable_KiB":15*1024*1024,"SwapFree_KiB":50000*1024,
      "memory_psi_avg10":0.2,"cpu_psi_avg10":0.1,"io_psi_avg10":0.0,
      "gpu_busy_percent":0,"mem_info_vram_total":8*1024**3,
      "mem_info_vram_used":6*1024**3,
    }
    records={}
    for idx,(mode,block) in enumerate(ORDER):
        depth={"cpu":0,"vulkan8":8}[mode]
        sequence=list(SOURCE_CASE_ORDER)
        random.Random(202611081646+block).shuffle(sequence)
        rows=[]
        for pos,name in enumerate(sequence):
            expected=f["tasks"][name]
            answer=canonical(expected["expected"])
            rows.append({
              "case_id":name,
              "case_prompt_sha256":sha(expected["prompt"]),
              "case_expected_sha256":sha(canonical(expected["expected"])),
              "id":f"synthetic-mode-{mode}-block-{block}-request-{pos}",
              "finish_reason":"stop","full_public_synthetic_answer":answer,
              "response_sha256":sha(answer),
              "response_first_100_chars":answer[:100],
              "semantic_oracle_pass":True,
              "http_wall_ms":2000.0,
              "http_plus_validation_wall_ms":2000.10,
              "validation_wall_ms":0.04,
              "wall_ms":2000.0,
              "usage":{"prompt_tokens":160,"completion_tokens":21,"total_tokens":181},
              "timings":{"predicted_n":21,
                "predicted_per_second":20.0,
                "predicted_ms":1000.0,
                "prompt_ms":900.0,"prompt_per_second":100.0},
              "pid":30000+idx,"process_start_ticks":600000+idx*100,
              "drm_client":str(5000+idx) if depth else "none",
              "drm_compute_delta_ns":10000000 if depth else None,
              "pre_pressure":0.1,"post_pressure":0.1,
              "post_vram_free_mib":1800.0,
            })
        records[(mode,block)]={
          "schema":"lfm3.deathstar.unstructured-triage-block-v1",
          "host":"deathstar-XPS-8920",
          "mode":mode,"block":block,"workload":"six_public_cases",
          "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
          "model_sha256_expected":MODEL_SHA,"model_sha256_observed":MODEL_SHA,
          "device":"none" if not depth else "Vulkan0",
          "offloaded_layers_requested":depth,
          "port":"127.0.0.1:19191",
          "alias":"lfm3-deathstar-unstructured-research",
          "requests_requested":6,"requests_attempted":6,"max_tokens":96,
          "case_order":sequence,
          "outcome":"PASS_RESEARCH_CAPTURE_WITH_QUALITY_SCORES",
          "production_authorized":False,
          "independent_tensor_execution_attested":False,
          "cpu_mode_gpu_compute_counter_verified":False,
          "credentials_read":False,
          "existing_services_modified":False,"public_ingress_enabled":False,
          "temporary_child_stopped":True,"temporary_log_removed":True,
          "temporary_port_closed":True,
          "preflight":copy.deepcopy(cap),"post_hash":copy.deepcopy(cap),
          "capacity_after_cleanup":copy.deepcopy(cap),
          "cooldown":[{"memory_psi":0.1,"gpu_busy":0,"seconds":float(i)}
                      for i in range(3)],
          "runtime_offload":{
            "summaries":[[depth,25],[depth,25]],
            "assigned_layers":list(range(17,25))*2 if depth else [],
            "final_vulkan_model_buffer_mib":435.64 if depth else None
          },
          "oracle_pass_count":6,"requests":rows,
        }
    return manifest,f,records

def replace_answer(stage,name,new_answer):
    r=next(x for x in stage["requests"] if x["case_id"]==name)
    r["full_public_synthetic_answer"]=new_answer
    r["response_sha256"]=sha(new_answer)
    r["response_first_100_chars"]=new_answer[:100]
    r["semantic_oracle_pass"]=False
    stage["oracle_pass_count"]-=1

class UnstructuredTriageAudit(unittest.TestCase):
    def test_valid_frozen_fixture_and_all_correct_is_research_only(self):
        m,f,r=bundle()
        self.assertTrue(_fixture_ok(f))
        out=audit_unstructured(m,f,r)
        self.assertEqual(out["decision"],"PASS_BOUNDED_UNSTRUCTURED_RESEARCH_ONLY")
        self.assertEqual(out["verified_requests"],24)
        self.assertEqual(out["model_modes"]["cpu"]["correct_exact"],12)
        self.assertEqual(out["model_modes"]["vulkan8"]["correct_exact"],12)
        self.assertFalse(out["production_authorized"])
        self.assertFalse(out["quality_generalizable"] if "quality_generalizable" in out else out["statistical_generalization_established"])

    def test_quality_failure_is_counted_not_suppressed(self):
        m,f,r=bundle()
        s=r[("vulkan8",1)]
        replace_answer(s,"sni",'{"category":"database_service_down","evidence":"E3"}')
        out=audit_unstructured(m,f,r)
        self.assertEqual(out["decision"],"PASS_BOUNDED_UNSTRUCTURED_RESEARCH_ONLY")
        self.assertEqual(out["model_modes"]["vulkan8"]["correct_exact"],11)
        self.assertEqual(out["model_modes"]["cpu"]["correct_exact"],12)

    def test_wrong_evidence_reference_fails_exact_oracle(self):
        m,f,r=bundle()
        self.assertFalse(exact_answer(
            '{"category":"tls_sni_missing","evidence":"E1"}',
            f["tasks"]["sni"]["expected"]))

    def test_duplicate_json_keys_denied(self):
        m,f,r=bundle()
        self.assertFalse(exact_answer(
            '{"category":"tls_sni_missing","category":"tls_sni_missing","evidence":"E3"}',
            f["tasks"]["sni"]["expected"]))
        self.assertFalse(exact_answer(
            '{"category":NaN,"category":"tls_sni_missing","evidence":"E3"}',
            f["tasks"]["sni"]["expected"]))

    def test_source_fixture_tampering_is_hold(self):
        m,f,r=bundle()
        f["tasks"]["database"]["source_note"]+=" mutated"
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")
        m,f,r=bundle()
        f["tasks"]["sni"]["expected"]["evidence"]="E2"
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")

    def test_missing_or_extra_stage_holds(self):
        m,f,r=bundle()
        r.pop(("vulkan8",2))
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")
        m,f,r=bundle()
        r[("cpu",3)]=copy.deepcopy(r[("cpu",2)])
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")

    def test_preregistration_order_and_pin_tamper_holds(self):
        m,f,r=bundle()
        m["primary_order"][0],m["primary_order"][1]=m["primary_order"][1],m["primary_order"][0]
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")
        m,f,r=bundle()
        m["script_sha256"]="a"*64
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")

    def test_prompt_and_ground_truth_hash_bound(self):
        m,f,r=bundle()
        r[("cpu",1)]["requests"][0]["case_prompt_sha256"]="a"*64
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")
        m,f,r=bundle()
        r[("vulkan8",2)]["requests"][0]["case_expected_sha256"]="b"*64
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")

    def test_invalid_response_hash_or_prefix_holds(self):
        m,f,r=bundle()
        r[("cpu",1)]["requests"][0]["response_sha256"]="a"*64
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")
        m,f,r=bundle()
        r[("cpu",2)]["requests"][0]["response_first_100_chars"]="different"
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")

    def test_claiming_wrong_answer_as_correct_holds(self):
        m,f,r=bundle()
        stage=r[("vulkan8",1)]
        replace_answer(stage,"pilot",'{ }')
        stage["oracle_pass_count"]=6
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")

    def test_unsafe_resource_psi_and_vram_holds(self):
        m,f,r=bundle()
        r[("cpu",2)]["preflight"]["memory_psi_avg10"]=4.16
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")
        m,f,r=bundle()
        r[("vulkan8",1)]["requests"][2]["post_vram_free_mib"]=100
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")

    def test_gpu_compute_client_and_offload_holds(self):
        m,f,r=bundle()
        r[("vulkan8",2)]["runtime_offload"]["summaries"][1]=[7,25]
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")
        m,f,r=bundle()
        r[("vulkan8",1)]["requests"][0]["drm_compute_delta_ns"]=0
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")
        m,f,r=bundle()
        r[("vulkan8",1)]["requests"][1]["drm_client"]="other"
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")

    def test_cpu_positive_compute_does_not_get_cpu_label(self):
        m,f,r=bundle()
        r[("cpu",1)]["requests"][0]["drm_compute_delta_ns"]=1
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")

    def test_forged_native_timing_rejected(self):
        m,f,r=bundle()
        r[("vulkan8",1)]["requests"][0]["timings"]["predicted_per_second"]=10000.
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")

    def test_end_to_end_timing_must_be_same_request_monotonic(self):
        m,f,r=bundle()
        r[("vulkan8",1)]["requests"][0]["http_plus_validation_wall_ms"]=100.
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")
        m,f,r=bundle()
        r[("cpu",2)]["requests"][0]["validation_wall_ms"]=float("nan")
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")

    def test_incomplete_cleanup_denies_benchmark(self):
        m,f,r=bundle()
        r[("cpu",2)]["temporary_port_closed"]=False
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")

    def test_replays_across_blocks_rejected(self):
        m,f,r=bundle()
        r[("cpu",2)]["requests"][0]["id"]=r[("cpu",1)]["requests"][0]["id"]
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")
        m,f,r=bundle()
        s=r[("vulkan8",1)]["requests"][0]
        for x in r[("vulkan8",2)]["requests"]:
            x["pid"]=s["pid"];x["process_start_ticks"]=s["process_start_ticks"]
        self.assertEqual(audit_unstructured(m,f,r)["decision"],"HOLD")

    def test_untrusted_types_hold_with_production_false(self):
        m,f,r=bundle()
        for bad in (None,[],True,{}):
            a=audit_unstructured(bad,f,r)
            b=audit_unstructured(m,bad,r)
            c=audit_unstructured(m,f,bad)
            for result in (a,b,c):
                self.assertEqual(result["decision"],"HOLD")
                self.assertFalse(result["production_authorized"])

if __name__=="__main__":unittest.main()
