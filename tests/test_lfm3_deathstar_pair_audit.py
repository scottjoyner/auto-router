"""Frozen offline CPU vs one-layer RX480 five-request comparisons, synthetic only."""
import copy
import hashlib
import json
import unittest
from research.lfm3_top5.deathstar_pair_audit import audit_deathstar_cpu_vulkan_pair, EXPECTED_SHA
from test_lfm3_layer_placement_audit import fixture as single_gpu_fixture

def synthetic_pair():
    one=single_gpu_fixture()
    gpu=copy.deepcopy(one)
    gpu.update({
        "schema":"lfm3.deathstar.vulkan1-five.same-qad.v1",
        "outcome":"PASS_VULKAN_ONE_LAYER_FIVE_RESEARCH_ONLY",
        "requested_generations":5,"attempted_generations":5,
    })
    gpu["observations"]=[]
    gpu.pop("receipt",None)
    gpu.pop("process_gpu_before",None)
    gpu.pop("process_gpu_after",None)
    gpu.pop("one_request_elapsed_ms",None)
    gpu.pop("compute_engine_delta_ns",None)
    for i in range(5):
        first=copy.deepcopy(one["process_gpu_before"])
        last=copy.deepcopy(one["process_gpu_after"])
        before=100000000+i*100000000
        after=before+95000000
        first["amd_clients"][0]["drm-engine-compute"]=str(before)+" ns"
        last["amd_clients"][0]["drm-engine-compute"]=str(after)+" ns"
        gpu["observations"].append({
            "id":"synthetic-gpu-"+str(i),
            "answer":"READY","finish_reason":"stop","elapsed_ms":420.0,
            "usage":copy.deepcopy(one["receipt"]["usage"]),
            "timings":copy.deepcopy(one["receipt"]["timings"]),
            "gpu_before":first,"gpu_after":last,"compute_delta_ns":95000000,
        })
    gpu["cumulative_request_compute_delta_ns"]=475000000

    cp=copy.deepcopy(one["capacity_preflight"])
    gpu["capacity_after_cleanup"]=copy.deepcopy(cp)
    alias="lfm3-deathstar-cpu-research"
    body={
        "model":alias,
        "messages":[{"role":"user","content":"Respond with exactly READY."}],
        "temperature":0,"max_tokens":24,"stream":False,
        "chat_template_kwargs":{"enable_thinking":False},
    }
    request_sha=hashlib.sha256(json.dumps(body,sort_keys=True,
        separators=(",",":")).encode()).hexdigest()
    cpu={
        "schema":"lfm3.deathstar.cpu-five.same-qad.v1",
        "host":"deathstar-XPS-8920",
        "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
        "checkpoint_expected_sha256":EXPECTED_SHA,
        "checkpoint_observed_sha256":EXPECTED_SHA,
        "device_selection":"none","gpu_layers_requested":0,
        "host_port":"127.0.0.1:19187","server_alias":alias,
        "requested_generations":5,"attempted_generations":5,
        "outcome":"PASS_CPU_CONTROL_RESEARCH_ONLY",
        "credentials_read":False,"existing_services_modified":False,
        "public_ingress_enabled":False,
        "gpu_acceleration_proven":False,"production_authorized":False,
        "temporary_child_stopped":True,"temporary_log_removed":True,
        "temporary_port_closed":True,
        "capacity_preflight":copy.deepcopy(cp),
        "capacity_after_hash":copy.deepcopy(cp),
        "capacity_after_cleanup":copy.deepcopy(cp),
        "request_hash":request_sha,
        "runtime_offload_summary":[],
        "observations":[],
    }
    for i in range(5):
        cpu["observations"].append({
            "id":"synthetic-cpu-"+str(i),"answer":"READY",
            "finish_reason":"stop","elapsed_ms":300.0,
            "usage":copy.deepcopy(one["receipt"]["usage"]),
            "timings":dict(one["receipt"]["timings"],
                           predicted_per_second=23.5,prompt_per_second=75.0),
        })
    return cpu,gpu

class DeathstarPair(unittest.TestCase):
    def check(self,cpu=None,gpu=None):
        if cpu is None and gpu is None:
            cpu,gpu=synthetic_pair()
        return audit_deathstar_cpu_vulkan_pair(
            cpu,gpu,expected_checkpoint_sha256=EXPECTED_SHA)

    def test_good_synthetic_pair_is_only_research(self):
        result=self.check()
        self.assertEqual(result["decision"],"PASS_PAIRED_SHORT_REQUEST_RESEARCH_ONLY")
        self.assertEqual(result["observations_cpu"],5)
        self.assertEqual(result["observations_vulkan"],5)
        self.assertEqual(result["cpu"]["median_native_decode_tps"],23.5)
        self.assertEqual(result["vulkan_one_layer"]["median_native_decode_tps"],14.844)
        self.assertTrue(result["all_five_drm_compute_delta_positive"])
        self.assertFalse(result["statistical_superiority_established"])
        self.assertFalse(result["production_authorized"])
        self.assertFalse(result["independent_gpu_tensor_execution_attested"])

    def test_wrong_file_digest_denied(self):
        c,g=synthetic_pair();c["checkpoint_observed_sha256"]="a"*64
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();g["checkpoint_observed_sha256"]="b"*64
        self.assertEqual(self.check(c,g)["decision"],"HOLD")

    def test_cpu_actual_offload_forbidden(self):
        c,g=synthetic_pair();c["gpu_layers_requested"]=1
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();c["device_selection"]="Vulkan0"
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();c["runtime_offload_summary"]=["offloaded 1/25 layers to GPU"]
        self.assertEqual(self.check(c,g)["decision"],"HOLD")

    def test_cpu_prompt_alias_identity_hash_pinned(self):
        c,g=synthetic_pair();c["request_hash"]="a"*64
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();c["server_alias"]="wrong-model"
        self.assertEqual(self.check(c,g)["decision"],"HOLD")

    def test_no_claim_from_missing_fifth_cpu_or_gpu_request(self):
        c,g=synthetic_pair();c["observations"].pop()
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();g["observations"].pop()
        self.assertEqual(self.check(c,g)["decision"],"HOLD")

    def test_request_ids_are_unique_per_run(self):
        c,g=synthetic_pair();c["observations"][1]["id"]=c["observations"][0]["id"]
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();g["observations"][1]["id"]=g["observations"][0]["id"]
        self.assertEqual(self.check(c,g)["decision"],"HOLD")

    def test_each_gpu_counter_must_be_live_and_same_client(self):
        c,g=synthetic_pair();g["observations"][3]["compute_delta_ns"]=0
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();g["observations"][3]["gpu_after"]["amd_clients"][0]["drm-client-id"]="99"
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();g["observations"][2]["gpu_after"]["pid"]+=1
        self.assertEqual(self.check(c,g)["decision"],"HOLD")

    def test_gpu_cumulative_receipt_must_match_individual_deltas(self):
        c,g=synthetic_pair();g["cumulative_request_compute_delta_ns"]+=1
        self.assertEqual(self.check(c,g)["decision"],"HOLD")

    def test_gpu_loader_must_repeat_same_one_of_25_layer(self):
        c,g=synthetic_pair();g["server_reported_offload_counts"][1]["layers"]=2
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();g["runtime_reported_assigned_layers"]=[23,24]
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();g["runtime_reported_vulkan_model_buffer_mib"]=0
        self.assertEqual(self.check(c,g)["decision"],"HOLD")

    def test_unsafe_pressure_or_failed_cleanup_blocks_pair(self):
        c,g=synthetic_pair();c["capacity_after_hash"]["memory_psi_avg10"]=4.16
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();g["capacity_before_request"]["memory_psi_avg10"]=4.16
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();c["temporary_port_closed"]=False
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();g["temporary_child_stopped"]=False
        self.assertEqual(self.check(c,g)["decision"],"HOLD")

    def test_tokens_and_rates_are_valid(self):
        c,g=synthetic_pair();c["observations"][2]["usage"]["total_tokens"]=21
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();g["observations"][4]["timings"]["predicted_per_second"]=float("nan")
        self.assertEqual(self.check(c,g)["decision"],"HOLD")

    def test_claimed_production_permit_rejected(self):
        c,g=synthetic_pair();g["production_authorized"]=True
        self.assertEqual(self.check(c,g)["decision"],"HOLD")
        c,g=synthetic_pair();c["production_authorized"]=True
        self.assertEqual(self.check(c,g)["decision"],"HOLD")

    def test_invalid_inputs_denied(self):
        c,g=synthetic_pair()
        for bad in (None,{},[],True):
            r=audit_deathstar_cpu_vulkan_pair(
                bad,g,expected_checkpoint_sha256=EXPECTED_SHA)
            self.assertEqual(r["decision"],"HOLD")
            r=audit_deathstar_cpu_vulkan_pair(
                c,bad,expected_checkpoint_sha256=EXPECTED_SHA)
            self.assertEqual(r["decision"],"HOLD")

if __name__=="__main__":unittest.main()
