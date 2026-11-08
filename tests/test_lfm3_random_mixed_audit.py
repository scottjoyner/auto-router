"""Synthetic-only adversarial checks for randomized Deathstar mixed workload.

No host evidence, process identifiers, request IDs or provider credentials are
included in this test module. Exact source hashes are pinned in pure audit.
"""
import copy
import unittest
from research.lfm3_top5.random_mixed_audit import (
    MODEL_SHA, SOURCE_SHA, SEED, GROUPS, WORKLOADS, MODES,
    plan, audit_block, audit_random_mixed,
)

def synthetic_bundle():
    ordered=plan()
    manifest={
        "schema":"lfm3.deathstar.mixed-random-prereg.v1",
        "host":"deathstar-XPS-8920",
        "preregistered_before_runs":True,
        "random_seed":SEED,
        "groups":[list(group) for group in GROUPS],
        "order":[{"mode":m,"block":b,"workload":w} for m,b,w in ordered],
        "script_sha256":SOURCE_SHA,
        "checkpoint_sha256":MODEL_SHA,
        "target_requests":24,
        "production_authorized":False,
        "independent_gpu_tensor_execution_attested":False,
        "statistical_superiority_established":False,
    }
    records={}
    for index,(mode,block,workload) in enumerate(ordered):
        layers={"cpu":0,"vulkan6":6,"vulkan8":8}[mode]
        budget=WORKLOADS[workload]["max_tokens"]
        prompt_n={"short":38,"long":283}[workload]
        pid=15000+index
        capacity={
            "MemAvailable_KiB":15*1024*1024,
            "SwapFree_KiB":128*1024*1024,
            "memory_psi_avg10":0.2,
            "cpu_psi_avg10":0.1,
            "io_psi_avg10":0.0,
            "gpu_busy_percent":0,
            "mem_info_vram_total":8*1024*1024*1024,
            "mem_info_vram_used":6*1024*1024*1024,
        }
        off={
            "summaries":[[layers,25]]*2,
            "assigned_layers":list(range(25-layers,25))*2,
            "final_vulkan_model_buffer_mib":{0:None,6:370.26,8:435.64}[layers],
        }
        rows=[]
        for i in range(2):
            tps={"cpu":23.0,"vulkan6":29.0,"vulkan8":31.0}[mode]
            rows.append({
                "id":f"synthetic-test-{mode}-{block}-{workload}-{i}",
                "finish_reason":"length",
                "response_sha256":"a"*64,
                "response_first_100_chars":"1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11",
                "wall_ms":budget/tps*1000+150,
                "usage":{
                    "prompt_tokens":prompt_n,
                    "completion_tokens":budget,
                    "total_tokens":prompt_n+budget,
                },
                "timings":{
                    "prompt_ms":120.0,
                    "prompt_per_second":100.0,
                    "predicted_ms":budget/tps*1000,
                    "predicted_per_second":tps,
                    "predicted_n":budget,
                },
                "pid":pid,
                "process_start_ticks":888888+index*100,
                "drm_client":str(25000+index) if layers else "none",
                "drm_compute_delta_ns":10000000+i*100000 if layers else None,
                "pre_pressure":0.1,
                "post_pressure":0.2,
                "post_vram_free_mib":1750.0,
            })
        records[(mode,block,workload)]={
            "schema":"lfm3.deathstar.mixed-random-block-v2",
            "host":"deathstar-XPS-8920",
            "mode":mode,"block":block,"workload":workload,
            "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
            "model_sha256_expected":MODEL_SHA,
            "model_sha256_observed":MODEL_SHA,
            "device":"none" if layers==0 else "Vulkan0",
            "offloaded_layers_requested":layers,
            "port":"127.0.0.1:19189",
            "alias":"lfm3-deathstar-mixed-research",
            "requests_requested":2,"requests_attempted":2,
            "max_tokens":budget,
            "prompt_sha256":WORKLOADS[workload]["prompt_sha256"],
            "request_sha256":WORKLOADS[workload]["request_sha256"],
            "outcome":"PASS_LONG_BLOCK_RESEARCH_ONLY",
            "production_authorized":False,
            "independent_tensor_execution_attested":False,
            "cpu_mode_gpu_compute_counter_verified":False,
            "credentials_read":False,
            "existing_services_modified":False,
            "public_ingress_enabled":False,
            "temporary_child_stopped":True,
            "temporary_log_removed":True,
            "temporary_port_closed":True,
            "preflight":copy.deepcopy(capacity),
            "post_hash":copy.deepcopy(capacity),
            "capacity_after_cleanup":copy.deepcopy(capacity),
            "cooldown":[{"seconds":float(n),"memory_psi":0.1,"gpu_busy":0}
                        for n in range(3)],
            "runtime_offload":off,
            "requests":rows,
        }
    return manifest,records

class MixedRandomTests(unittest.TestCase):
    def check(self,m=None,rs=None):
        if m is None and rs is None:
            m,rs=synthetic_bundle()
        return audit_random_mixed(m,rs)

    def test_pass_is_explicitly_research_only(self):
        r=self.check()
        self.assertEqual(r["decision"],"PASS_RANDOM_MIXED_RESEARCH_ONLY")
        self.assertEqual(r["generations_verified"],24)
        self.assertEqual(r["blocks_verified"],12)
        self.assertEqual(r["sampled_output_length_tokens"],{"short":64,"long":160})
        self.assertFalse(r["significant_superiority_established"])
        self.assertFalse(r["independent_kernel_execution_attested"])
        self.assertFalse(r["production_authorized"])

    def test_all_twelve_blocks_independently_validate(self):
        _,data=synthetic_bundle()
        for mode,block,workload in plan():
            result=audit_block(data[(mode,block,workload)],mode,block,workload)
            self.assertEqual(result["decision"],"PASS_MIXED_BLOCK_RESEARCH_ONLY")

    def test_preregistered_order_tampering_fails(self):
        m,rs=synthetic_bundle()
        m["order"][0],m["order"][1]=m["order"][1],m["order"][0]
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")

    def test_missing_block_fails(self):
        m,rs=synthetic_bundle()
        rs.pop(plan()[1])
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")

    def test_wrong_seed_and_source_hash(self):
        m,rs=synthetic_bundle();m["random_seed"]+=1
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")
        m,rs=synthetic_bundle();m["script_sha256"]="b"*64
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")

    def test_wrong_model_hash(self):
        m,rs=synthetic_bundle()
        rs[("vulkan8",1,"short")]["model_sha256_observed"]="c"*64
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")

    def test_wrong_or_swapped_request_content(self):
        m,rs=synthetic_bundle()
        rs[("vulkan6",1,"short")]["request_sha256"]=WORKLOADS["long"]["request_sha256"]
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")
        m,rs=synthetic_bundle()
        rs[("cpu",2,"long")]["prompt_sha256"]="a"*64
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")

    def test_missing_tokens_and_short_output_fails(self):
        m,rs=synthetic_bundle()
        rs[("cpu",1,"short")]["requests"][0]["usage"]["completion_tokens"]=3
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")
        m,rs=synthetic_bundle()
        rs[("vulkan6",2,"long")]["max_tokens"]=64
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")

    def test_reasoning_or_garbled_output_fails(self):
        m,rs=synthetic_bundle()
        rs[("cpu",1,"short")]["requests"][0]["response_first_100_chars"]="<think>Let me reason."
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")
        m,rs=synthetic_bundle()
        rs[("vulkan8",2,"long")]["requests"][0]["response_first_100_chars"]="The output is 1, 2, 3"
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")

    def test_replay_id_across_modes_or_within_one_block(self):
        m,rs=synthetic_bundle()
        rs[("vulkan6",1,"short")]["requests"][1]["id"] = rs[("cpu",1,"short")]["requests"][0]["id"]
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")
        m,rs=synthetic_bundle()
        rs[("vulkan8",2,"long")]["requests"][1]["id"] = rs[("vulkan8",2,"long")]["requests"][0]["id"]
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")

    def test_pid_reuse_across_stages(self):
        m,rs=synthetic_bundle()
        first=rs[("cpu",1,"short")]["requests"][0]
        for row in rs[("cpu",2,"short")]["requests"]:
            row["pid"]=first["pid"]
            row["process_start_ticks"]=first["process_start_ticks"]
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")

    def test_gpu_counter_and_client_failures(self):
        m,rs=synthetic_bundle()
        rs[("vulkan8",1,"short")]["requests"][0]["drm_compute_delta_ns"]=0
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")
        m,rs=synthetic_bundle()
        rs[("vulkan6",2,"long")]["requests"][1]["drm_client"]="2000"
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")

    def test_cpu_positive_gpu_activity_denied(self):
        m,rs=synthetic_bundle()
        rs[("cpu",1,"long")]["requests"][0]["drm_compute_delta_ns"]=10000
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")

    def test_wrong_loader_layer_and_buffer(self):
        m,rs=synthetic_bundle()
        rs[("vulkan8",2,"short")]["runtime_offload"]["summaries"][1]=[7,25]
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")
        m,rs=synthetic_bundle()
        rs[("vulkan6",2,"long")]["runtime_offload"]["assigned_layers"][0]=1
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")

    def test_pressures_denied(self):
        m,rs=synthetic_bundle()
        rs[("cpu",1,"short")]["preflight"]["memory_psi_avg10"]=4.16
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")
        m,rs=synthetic_bundle()
        rs[("vulkan8",1,"long")]["requests"][1]["post_vram_free_mib"]=100
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")

    def test_cleanup_or_credentials_boundary(self):
        m,rs=synthetic_bundle()
        rs[("vulkan6",2,"short")]["temporary_port_closed"]=False
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")
        m,rs=synthetic_bundle()
        rs[("cpu",2,"long")]["credentials_read"]=True
        self.assertEqual(self.check(m,rs)["decision"],"HOLD")

    def test_untrusted_types_rejected(self):
        m,rs=synthetic_bundle()
        for invalid in (None,{},[],True):
            a=self.check(invalid,rs)
            b=self.check(m,invalid)
            self.assertEqual(a["decision"],"HOLD")
            self.assertEqual(b["decision"],"HOLD")
            self.assertFalse(a["production_authorized"])

if __name__=="__main__":
    unittest.main()
