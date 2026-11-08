"""Synthetic, immutable Deathstar runtime layer+counter claims; no live hardware."""
import copy
import unittest
from research.lfm3_top5.gpu_request_audit import (
    assess_deathstar_runtime_layer_placement,EXPECTED_SHA,
)

def fixture():
    cap={
        "MemAvailable_KiB":16*1024*1024,"SwapFree_KiB":2*1024*1024,
        "memory_psi_avg10":0.4,"cpu_psi_avg10":0.0,"io_psi_avg10":0.0,
        "gpu_busy_percent":0,
        "mem_info_vram_total":8589934592,"mem_info_vram_used":6252916736,
    }
    before={"pid":40122,"startticks":610000,
        "amd_clients":[{
            "device":"/dev/dri/renderD128","drm-driver":"amdgpu",
            "drm-pdev":"0000:01:00.0","drm-client-id":"33",
            "drm-engine-compute":"100000000 ns",
            "drm-resident-vram":"1180 KiB",
        }]}
    after=copy.deepcopy(before)
    after["amd_clients"][0]["drm-engine-compute"]="195000000 ns"
    lines=[
        "I llama_prepare_model_devices: using device Vulkan0 (AMD Radeon RX 480 Graphics (RADV POLARIS10)) (0000:01:00.0) - 1980 MiB free",
        "D load_tensors: layer  24 assigned to device Vulkan0, is_swa = 0",
        "I load_tensors: offloading output layer to GPU",
        "I load_tensors: offloading 0 repeating layers to GPU",
        "I load_tensors: offloaded 1/25 layers to GPU",
        "I load_tensors: Vulkan0 model buffer size = 0.00 MiB",
        "I sched_reserve: Vulkan0 compute buffer size = 61.44 MiB",
        "I llama_prepare_model_devices: using device Vulkan0 (AMD Radeon RX 480 Graphics (RADV POLARIS10)) (0000:01:00.0) - 1980 MiB free",
        "D load_tensors: layer  24 assigned to device Vulkan0, is_swa = 0",
        "I load_tensors: offloading output layer to GPU",
        "I load_tensors: offloading 0 repeating layers to GPU",
        "I load_tensors: offloaded 1/25 layers to GPU",
        "I load_tensors: Vulkan0 model buffer size = 205.09 MiB",
        "I sched_reserve: Vulkan0 compute buffer size = 61.44 MiB",
    ]
    return {
        "schema":"lfm3.deathstar.vulkan1-request.final-layer-witness.v5",
        "host":"deathstar-XPS-8920",
        "outcome":"PASS_GPU_CONTEXT_CORRELATED_RESEARCH_ONLY",
        "checkpoint_expected_sha256":EXPECTED_SHA,
        "checkpoint_observed_sha256":EXPECTED_SHA,
        "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
        "gpu_device_requested":"Vulkan0","gpu_layers_requested":1,
        "new_service_binding":"127.0.0.1:19186",
        "attempted_generations":1,"credentials_read":False,
        "existing_services_modified":False,"new_public_ingress":False,
        "gpu_token_execution_independently_attested":False,
        "production_authorized":False,
        "temporary_child_stopped":True,"temporary_log_removed":True,
        "port_closed_after_cleanup":True,
        "capacity_preflight":copy.deepcopy(cap),
        "capacity_after_hash":copy.deepcopy(cap),
        "capacity_after_wait":copy.deepcopy(cap),
        "capacity_before_request":copy.deepcopy(cap),
        "process_gpu_before":before,"process_gpu_after":after,
        "compute_engine_delta_ns":95000000,"one_request_elapsed_ms":509.475,
        "receipt":{
            "id":"chatcmpl-synthetic-1",
            "model_alias":"lfm3-deathstar-vulkan1-research",
            "finish_reason":"stop","answer":"READY",
            "usage":{"prompt_tokens":17,"completion_tokens":3,"total_tokens":20},
            "timings":{"prompt_n":17,"predicted_n":3,"prompt_ms":368.317,
                       "predicted_ms":134.735,"prompt_per_second":46.156,
                       "predicted_per_second":14.844},
        },
        "server_reported_offload_counts":[
            {"layers":1,"layers_total":25},
            {"layers":1,"layers_total":25},
        ],
        "runtime_reported_output_layer_gpu":True,
        "runtime_reported_assigned_layers":[24,24],
        "runtime_reported_vulkan_model_buffer_mib":205.09,
        "startup_offload_lines":lines,
    }

class LayerPlacement(unittest.TestCase):
    def check(self,d=None):
        return assess_deathstar_runtime_layer_placement(
            fixture() if d is None else d,
            expected_checkpoint_sha256=EXPECTED_SHA)

    def test_positive_duplicate_init_valid_and_only_research(self):
        x=self.check()
        self.assertEqual(x["decision"],"PASS_RUNTIME_REPORTED_OUTPUT_LAYER_RESEARCH_ONLY")
        self.assertTrue(x["runtime_reported_gpu_output_layer"])
        self.assertEqual(x["runtime_reported_offloaded_layers"],1)
        self.assertEqual(x["runtime_reported_model_layers"],25)
        self.assertEqual(x["runtime_reported_assigned_layer"],24)
        self.assertEqual(x["runtime_reported_vulkan_model_buffer_mib"],205.09)
        self.assertFalse(x["independent_tensor_execution_attested"])
        self.assertFalse(x["production_authorized"])

    def test_contradictory_second_offload_denied(self):
        d=fixture()
        d["startup_offload_lines"][11]="I load_tensors: offloaded 0/25 layers to GPU"
        self.assertEqual(self.check(d)["decision"],"HOLD")
        d=fixture()
        d["startup_offload_lines"][11]="I load_tensors: offloaded 2/25 layers to GPU"
        self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_claimed_count_can_not_contradict_log(self):
        d=fixture();d["server_reported_offload_counts"][1]["layers"]=2
        self.assertEqual(self.check(d)["decision"],"HOLD")
        d=fixture();d["server_reported_offload_counts"]=[]
        self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_assignment_to_cpu_or_wrong_layer_rejected(self):
        d=fixture();d["startup_offload_lines"][8]="D load_tensors: layer 24 assigned to device CPU"
        self.assertEqual(self.check(d)["decision"],"HOLD")
        d=fixture();d["runtime_reported_assigned_layers"]=[24,23]
        self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_nonzero_model_buffer_must_match_raw_log(self):
        d=fixture();d["startup_offload_lines"][12]="I load_tensors: Vulkan0 model buffer size = 0.00 MiB"
        self.assertEqual(self.check(d)["decision"],"HOLD")
        d=fixture();d["runtime_reported_vulkan_model_buffer_mib"]=256.0
        self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_wrong_device_or_missing_output_claim(self):
        d=fixture();d["startup_offload_lines"][0]="I device Vulkan1 Intel"
        d["startup_offload_lines"][7]="I device Vulkan1 Intel"
        self.assertEqual(self.check(d)["decision"],"HOLD")
        d=fixture()
        d["startup_offload_lines"][2]="I no output layer offloaded"
        d["startup_offload_lines"][9]="I no output layer offloaded"
        self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_request_counter_and_pid_must_still_match(self):
        d=fixture();d["compute_engine_delta_ns"]=5
        self.assertEqual(self.check(d)["decision"],"HOLD")
        d=fixture();d["process_gpu_after"]["startticks"]+=1
        self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_no_credential_or_prod_claim(self):
        for k in ("credentials_read","existing_services_modified",
                  "new_public_ingress","production_authorized",
                  "gpu_token_execution_independently_attested"):
            d=fixture();d[k]=True
            self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_cleanup_and_pressure_gate_mandatory(self):
        d=fixture();d["port_closed_after_cleanup"]=False
        self.assertEqual(self.check(d)["decision"],"HOLD")
        d=fixture();d["capacity_before_request"]["memory_psi_avg10"]=4.16
        self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_reject_secret_bearing_log_line(self):
        d=fixture();d["startup_offload_lines"].append("authorization: bearer REDACTED")
        self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_wrong_schema_and_digest(self):
        d=fixture();d["schema"]="lfm3.deathstar.vulkan1-request.final-layer-witness.v4"
        self.assertEqual(self.check(d)["decision"],"HOLD")
        d=fixture();d["checkpoint_observed_sha256"]="a"*64
        self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_malformed_inputs(self):
        for d in ({},[],None,True):
            x=assess_deathstar_runtime_layer_placement(
                d,expected_checkpoint_sha256=EXPECTED_SHA)
            self.assertEqual(x["decision"],"HOLD")
            self.assertFalse(x["independent_tensor_execution_attested"])
            self.assertFalse(x["production_authorized"])

if __name__=="__main__":unittest.main()
