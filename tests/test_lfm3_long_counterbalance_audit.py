"""Offline synthetic 112-token CPU / 6-layer / 8-layer six-block safety gates."""
import copy
import re
import unittest
from research.lfm3_top5.long_counterbalance_audit import (
    ORDER, MODEL_PIN, ALIAS, PROMPT_SHA, REQUEST_SHA,
    audit_long_block, audit_counterbalanced_long,
)

def fake_receipt(mode,block):
    depth={"cpu":0,"vulkan6":6,"vulkan8":8}[mode]
    cap={
        "MemAvailable_KiB":16*1024*1024,
        "SwapFree_KiB":2*1024*1024,
        "memory_psi_avg10":0.0,"cpu_psi_avg10":0.0,
        "io_psi_avg10":0.0,"gpu_busy_percent":0,
        "mem_info_vram_total":8*1024**3,
        "mem_info_vram_used":6*1024**3,
    }
    offload={
        "summaries":[[depth,25],[depth,25]] if depth else [],
        "assigned_layers":list(range(25-depth,25))*2 if depth else [],
        "final_vulkan_model_buffer_mib":{0:None,6:370.26,8:435.64}[depth],
    }
    rows=[]
    for i in range(3):
        rates={"cpu":23.0,"vulkan6":29.0,"vulkan8":30.0}
        rows.append({
            "id":f"synthetic-{mode}-block{block}-request{i}",
            "finish_reason":"length",
            "response_sha256":"a"*64,
            "response_first_100_chars":"1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11",
            "wall_ms":{"cpu":5200.0,"vulkan6":4200.0,"vulkan8":4000.0}[mode],
            "usage":{"prompt_tokens":50,"completion_tokens":112,"total_tokens":162},
            "timings":{
                "prompt_ms":150.0,"prompt_per_second":120.0,
                "predicted_ms":5000.0,"predicted_per_second":rates[mode],
                "predicted_n":112
            },
            "pid":10000+block*100+depth,
            "process_start_ticks":700000+block*100+depth,
            "drm_client":str(700+depth+block) if depth else "1",
            "drm_compute_delta_ns":10000000+1000*i if depth else None,
            "pre_pressure":0.1,"post_pressure":0.2,
            "post_vram_free_mib":1750.0,
        })
    return {
        "schema":"lfm3.deathstar.long-order-v3",
        "host":"deathstar-XPS-8920",
        "mode":mode,"block":block,
        "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
        "model_sha256_expected":MODEL_PIN,
        "model_sha256_observed":MODEL_PIN,
        "device":"none" if mode=="cpu" else "Vulkan0",
        "offloaded_layers_requested":depth,
        "port":"127.0.0.1:19188",
        "alias":ALIAS,
        "requests_requested":3,"requests_attempted":3,
        "max_tokens":112,"prompt_sha256":PROMPT_SHA,
        "request_sha256":REQUEST_SHA,
        "outcome":"PASS_LONG_BLOCK_RESEARCH_ONLY",
        "production_authorized":False,
        "independent_tensor_execution_attested":False,
        "cpu_mode_gpu_compute_counter_verified":False,
        "credentials_read":False,"existing_services_modified":False,
        "public_ingress_enabled":False,
        "temporary_child_stopped":True,
        "temporary_log_removed":True,
        "temporary_port_closed":True,
        "preflight":copy.deepcopy(cap),
        "post_hash":copy.deepcopy(cap),
        "capacity_after_cleanup":copy.deepcopy(cap),
        "cooldown":[{"seconds":float(i),"memory_psi":0.1,
                     "gpu_busy":0} for i in range(3)],
        "runtime_offload":offload,
        "requests":rows,
    }

def fake_sweep():
    return {pair:fake_receipt(*pair) for pair in ORDER}

class LongCounterbalance(unittest.TestCase):
    def test_good_synthetic_is_research_only(self):
        r=audit_counterbalanced_long(fake_sweep())
        self.assertEqual(r["decision"],"PASS_LONG_COUNTERBALANCED_RESEARCH_ONLY")
        self.assertEqual(r["blocks_verified"],6)
        self.assertEqual(r["total_generations"],18)
        self.assertEqual(r["results"]["vulkan8"]["generated_tokens"],672)
        self.assertEqual(r["observed_fastest_native_decode_mode"],"vulkan8")
        self.assertFalse(r["independent_gpu_tensor_execution_attested"])
        self.assertFalse(r["statistical_superiority_established"])
        self.assertFalse(r["cpu_gpu_compute_zero_independently_verified"])
        self.assertFalse(r["production_authorized"])

    def test_each_block_can_validate_alone(self):
        for mode,block in ORDER:
            d=fake_receipt(mode,block)
            self.assertEqual(
                audit_long_block(d,mode=mode,block=block)["decision"],
                "PASS_LONG_BLOCK_RESEARCH_ONLY")

    def test_missing_block_fails_closed(self):
        rs=fake_sweep();del rs[("cpu",2)]
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")

    def test_checkpoint_and_prompt_are_bound(self):
        for key in ("model_sha256_observed","model_sha256_expected",
                    "prompt_sha256","request_sha256"):
            rs=fake_sweep();rs[("vulkan8",2)][key]="b"*64
            self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")

    def test_workload_size_cannot_shrink(self):
        rs=fake_sweep()
        rs[("cpu",1)]["requests"][0]["usage"]["completion_tokens"]=3
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")
        rs=fake_sweep()
        rs[("vulkan6",1)]["max_tokens"]=24
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")

    def test_gpu_offload_assignment_exactly_matches(self):
        rs=fake_sweep()
        rs[("vulkan8",1)]["runtime_offload"]["summaries"][1]=[7,25]
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")
        rs=fake_sweep()
        rs[("vulkan6",1)]["runtime_offload"]["assigned_layers"][0]=17
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")

    def test_cpu_gpu_offload_denied(self):
        rs=fake_sweep()
        rs[("cpu",2)]["offloaded_layers_requested"]=1
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")
        rs=fake_sweep()
        rs[("cpu",2)]["requests"][1]["drm_compute_delta_ns"]=100000
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")

    def test_gpu_counter_missing_and_client_replaced(self):
        rs=fake_sweep()
        rs[("vulkan8",2)]["requests"][1]["drm_compute_delta_ns"]=0
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")
        rs=fake_sweep()
        rs[("vulkan6",1)]["requests"][2]["drm_client"]="998"
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")

    def test_stale_process_or_duplicate_request_id_denied(self):
        rs=fake_sweep()
        rs[("vulkan6",2)]["requests"][2]["process_start_ticks"]+=1
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")
        rs=fake_sweep()
        rs[("vulkan8",2)]["requests"][0]["id"]=rs[("vulkan6",2)]["requests"][0]["id"]
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")

    def test_missing_cleanup_holds(self):
        for k in ("temporary_child_stopped","temporary_log_removed",
                  "temporary_port_closed"):
            rs=fake_sweep();rs[("vulkan8",1)][k]=False
            self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")

    def test_pressure_psi_and_vram_headroom(self):
        rs=fake_sweep()
        rs[("cpu",1)]["cooldown"][-1]["memory_psi"]=4.16
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")
        rs=fake_sweep()
        rs[("vulkan6",1)]["requests"][0]["post_vram_free_mib"]=200
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")
        rs=fake_sweep()
        rs[("vulkan8",2)]["post_hash"]["memory_psi_avg10"]=4.16
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")

    def test_bad_native_rate_and_usage(self):
        rs=fake_sweep()
        rs[("cpu",1)]["requests"][0]["timings"]["predicted_per_second"]=float("nan")
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")
        rs=fake_sweep()
        rs[("vulkan6",1)]["requests"][1]["usage"]["total_tokens"]=999
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")

    def test_answer_prefix_is_checked(self):
        rs=fake_sweep()
        rs[("vulkan8",1)]["requests"][0]["response_first_100_chars"]="garbled answer"
        self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")

    def test_forced_production_or_credential_access_holds(self):
        for key in ("production_authorized","credentials_read",
                    "independent_tensor_execution_attested"):
            rs=fake_sweep();rs[("vulkan8",2)][key]=True
            self.assertEqual(audit_counterbalanced_long(rs)["decision"],"HOLD")

    def test_untrusted_types_holds(self):
        for invalid in (None,{},[],True,1):
            r=audit_counterbalanced_long(invalid)
            self.assertEqual(r["decision"],"HOLD")
            self.assertFalse(r["production_authorized"])

if __name__=="__main__":unittest.main()
