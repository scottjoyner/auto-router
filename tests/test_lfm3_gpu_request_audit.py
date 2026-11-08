"""Synthetic adversarial AMD DRM request-correlation tests."""
import copy,unittest
from research.lfm3_top5.gpu_request_audit import assess_deathstar_gpu_request,EXPECTED_SHA

def fixture():
    capacity={
        "MemAvailable_KiB":16*1024*1024,
        "SwapFree_KiB":20*1024*1024,
        "memory_psi_avg10":0.7,
        "cpu_psi_avg10":0.0,
        "io_psi_avg10":0.2,
        "gpu_busy_percent":0,
        "mem_info_vram_total":8*1024**3,
        "mem_info_vram_used":6*1024**3,
    }
    before={"pid":4321,"startticks":1002233,
            "amd_clients":[{"device":"/dev/dri/renderD128","drm-driver":"amdgpu",
                "drm-pdev":"0000:01:00.0","drm-client-id":"32612",
                "drm-engine-compute":"67066921 ns","drm-resident-vram":"1180 KiB"}]}
    after=copy.deepcopy(before)
    after["amd_clients"][0]["drm-engine-compute"]="162142265 ns"
    return {"schema":"lfm3.deathstar.vulkan1-request.v1",
        "host":"deathstar-XPS-8920","outcome":"PASS_GPU_CONTEXT_CORRELATED_RESEARCH_ONLY",
        "checkpoint_expected_sha256":EXPECTED_SHA,
        "checkpoint_observed_sha256":EXPECTED_SHA,
        "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
        "gpu_device_requested":"Vulkan0","gpu_layers_requested":1,
        "new_service_binding":"127.0.0.1:19186","attempted_generations":1,
        "credentials_read":False,"existing_services_modified":False,
        "new_public_ingress":False,"gpu_token_execution_independently_attested":False,
        "production_authorized":False,
        "temporary_child_stopped":True,"temporary_log_removed":True,
        "port_closed_after_cleanup":True,
        "capacity_preflight":copy.deepcopy(capacity),
        "capacity_after_hash":copy.deepcopy(capacity),
        "capacity_after_wait":copy.deepcopy(capacity),
        "capacity_before_request":copy.deepcopy(capacity),
        "process_gpu_before":before,"process_gpu_after":after,
        "compute_engine_delta_ns":95075344,"one_request_elapsed_ms":465.285,
        "receipt":{"id":"chatcmpl-synthetic-001",
          "model_alias":"lfm3-deathstar-vulkan1-research",
          "finish_reason":"stop","answer":"READY",
          "usage":{"prompt_tokens":17,"completion_tokens":3,"total_tokens":20},
          "timings":{"prompt_n":17,"predicted_n":3,
             "prompt_ms":318.97,"predicted_ms":131.169,
             "prompt_per_second":53.296,"predicted_per_second":15.248}},
    }

class RequestGpuGate(unittest.TestCase):
    def check(self,v=None,expected=EXPECTED_SHA):
        return assess_deathstar_gpu_request(fixture() if v is None else v,
                                             expected_checkpoint_sha256=expected)

    def test_good_synthetic_correlation_is_research_only(self):
        r=self.check()
        self.assertEqual(r["decision"],"PASS_CORRELATED_GPU_PROCESS_RESEARCH_ONLY")
        self.assertTrue(r["correlated_process_gpu_activity"])
        self.assertEqual(r["compute_engine_delta_ns"],95075344)
        self.assertFalse(r["actual_gpu_tensor_layer_placement_verified"])
        self.assertFalse(r["independent_gpu_execution_attested"])
        self.assertFalse(r["production_authorized"])

    def test_model_digest_and_endpoint_pinned(self):
        self.assertEqual(self.check(expected="a"*64)["decision"],"HOLD")
        for k,v in (("checkpoint_observed_sha256","b"*64),
                    ("new_service_binding","0.0.0.0:19186"),
                    ("gpu_device_requested","Vulkan1"),
                    ("model_basename","other.gguf")):
            d=fixture();d[k]=v
            self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_compute_delta_required_and_exacts(self):
        for n in (0,-1,True,95075345):
            d=fixture();d["compute_engine_delta_ns"]=n
            self.assertEqual(self.check(d)["decision"],"HOLD")
        d=fixture();d["process_gpu_after"]["amd_clients"][0]["drm-engine-compute"]="67066921 ns"
        self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_pid_reuse_client_replacement_and_other_gpu_rejected(self):
        for key,val in (("pid",1234),("startticks",77777)):
            d=fixture();d["process_gpu_after"][key]=val
            self.assertEqual(self.check(d)["decision"],"HOLD")
        for key,val in (("drm-client-id","9"),("drm-pdev","0000:00:02.0"),
                        ("drm-driver","i915"),("device","/dev/dri/renderD129")):
            d=fixture();d["process_gpu_after"]["amd_clients"][0][key]=val
            self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_high_pressure_and_low_vram_rejected(self):
        d=fixture();d["capacity_before_request"]["memory_psi_avg10"]=4.16
        self.assertEqual(self.check(d)["decision"],"HOLD")
        d=fixture();d["capacity_after_hash"]["gpu_busy_percent"]=100
        self.assertEqual(self.check(d)["decision"],"HOLD")
        d=fixture();d["capacity_before_request"]["mem_info_vram_used"]=8*1024**3-100
        self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_response_must_be_complete_stop(self):
        for key,val in (("finish_reason","length"),("answer","WRONG"),
                        ("model_alias","lfm3-other"),("id","")):
            d=fixture();d["receipt"][key]=val
            self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_token_rates_counts_bounded(self):
        d=fixture();d["receipt"]["usage"]["total_tokens"]=21
        self.assertEqual(self.check(d)["decision"],"HOLD")
        d=fixture();d["receipt"]["timings"]["predicted_per_second"]=float("nan")
        self.assertEqual(self.check(d)["decision"],"HOLD")
        d=fixture();d["receipt"]["timings"]["predicted_n"]=4
        self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_cleanup_failure_rejected(self):
        for key in ("temporary_child_stopped","temporary_log_removed",
                    "port_closed_after_cleanup"):
            d=fixture();d[key]=False
            self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_authority_claims_never_promote(self):
        for k in ("gpu_token_execution_independently_attested",
                  "production_authorized","credentials_read",
                  "existing_services_modified","new_public_ingress"):
            d=fixture();d[k]=True
            self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_no_global_speedup_claim_from_one_request(self):
        r=self.check()
        self.assertNotIn("speedup",r)
        self.assertNotIn("gpu_tensor_layers_verified",r)
        self.assertFalse(r["actual_gpu_tensor_layer_placement_verified"])

    def test_invalid_layers_and_retries_held(self):
        for n in (0,5,True):
            d=fixture();d["gpu_layers_requested"]=n
            self.assertEqual(self.check(d)["decision"],"HOLD")
        d=fixture();d["attempted_generations"]=2
        self.assertEqual(self.check(d)["decision"],"HOLD")

    def test_malformed_fails_closed(self):
        for obj in ({},[],True,False):
            result=assess_deathstar_gpu_request(obj,expected_checkpoint_sha256=EXPECTED_SHA)
            self.assertEqual(result["decision"],"HOLD")
        result=assess_deathstar_gpu_request(None,expected_checkpoint_sha256=EXPECTED_SHA)
        self.assertEqual(result["decision"],"HOLD")

if __name__=="__main__":unittest.main()
