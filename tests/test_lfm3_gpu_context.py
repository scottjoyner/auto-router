"""Frozen public/synthetic process/driver evidence, NO actual GPU generation."""
import copy
import unittest
from research.lfm3_top5.benchmark_report import classify_existing_gpu_context


def client(pdev="0000:03:00.0",compute="1205966343762 ns"):
    return {
        "device":"/dev/dri/renderD128",
        "metrics":{"drm-driver":"amdgpu","drm-client-id":"12345",
                   "drm-pdev":pdev,"drm-resident-vram":"5859 MiB",
                   "drm-engine-compute":compute,"drm-engine-gfx":"11935249329 ns"}
    }


def fixture():
    sample={"pid":4242,"process_starttime_ticks":42424242,
            "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
            "alias":"lfm3-1.7b-research","port":"19175",
            "host":"127.0.0.1","gpu_layers_requested":"99",
            "drm_clients":[client()]}
    return {"schema":"lfm3.joyner.gpu-process.read-only.v1",
            "hostname":"joyner","before":copy.deepcopy(sample),
            "after":copy.deepcopy(sample),
            "http_status_no_credentials":{"/health":200,"/v1/models":401},
            "backend_dispatch_correlated_to_tokens":False,
            "authenticated_generation_receipt":False,
            "model_checkpoint_sha256_verified":False,
            "independent_gpu_execution_witness":False,
            "production_authorized":False}


class ExistingGpuContext(unittest.TestCase):
    def call(self,data=None):
        return classify_existing_gpu_context(fixture() if data is None else data)

    def test_observed_gpu_context_is_not_token_execution_proof(self):
        x=self.call()
        self.assertEqual(x["decision"],"HOLD")
        self.assertEqual(x["reason"],"gpu_context_present_unproven_tokens")
        self.assertTrue(x["gpu_context_observed"])
        self.assertTrue(x["historical_compute_engine_time_observed"])
        self.assertFalse(x["accelerator_verified"])
        self.assertFalse(x["production_authorized"])

    def test_historical_compute_counters_did_not_advance(self):
        x=self.call()
        self.assertEqual(x["compute_delta_ns"],0)

    def test_pid_reuse_or_process_replacement_invalidates_context(self):
        x=fixture();x["after"]["process_starttime_ticks"]+=1
        self.assertFalse(self.call(x)["gpu_context_observed"])
        x=fixture();x["after"]["pid"]+=1
        self.assertFalse(self.call(x)["gpu_context_observed"])

    def test_gpu_client_or_device_changed_invalidates_context(self):
        x=fixture();x["after"]["drm_clients"][0]["metrics"]["drm-client-id"]="other"
        self.assertFalse(self.call(x)["gpu_context_observed"])
        x=fixture();x["before"]["drm_clients"][0]["metrics"]["drm-pdev"]="0000:10:00.0"
        self.assertFalse(self.call(x)["gpu_context_observed"])

    def test_gpu_flag_only_cannot_fake_compute(self):
        x=fixture();x["before"]["drm_clients"]=[]
        self.assertFalse(self.call(x)["gpu_context_observed"])
        self.assertFalse(self.call(x)["accelerator_verified"])

    def test_counter_rollback_does_not_show_gpu_execution(self):
        x=fixture()
        x["after"]["drm_clients"][0]["metrics"]["drm-engine-compute"]="3 ns"
        self.assertFalse(self.call(x)["gpu_context_observed"])

    def test_counter_increase_without_correlated_authenticated_request_still_hold(self):
        x=fixture()
        x["after"]["drm_clients"][0]["metrics"]["drm-engine-compute"]="1205967343762 ns"
        r=self.call(x)
        self.assertTrue(r["gpu_context_observed"])
        self.assertGreater(r["compute_delta_ns"],0)
        self.assertFalse(r["accelerator_verified"])
        self.assertEqual(r["decision"],"HOLD")

    def test_missing_auth_is_not_bypassed_by_health(self):
        x=fixture();x["http_status_no_credentials"]["/v1/models"]=200
        self.assertEqual(self.call(x)["decision"],"HOLD")
        x=fixture();x["http_status_no_credentials"]["/health"]=401
        self.assertEqual(self.call(x)["decision"],"HOLD")

    def test_tampered_claim_of_authorized_token_execution_fails_closed(self):
        for key in ("production_authorized",
                    "backend_dispatch_correlated_to_tokens",
                    "authenticated_generation_receipt",
                    "independent_gpu_execution_witness",
                    "model_checkpoint_sha256_verified"):
            x=fixture();x[key]=True
            self.assertFalse(self.call(x)["accelerator_verified"])
            self.assertEqual(self.call(x)["decision"],"HOLD")

    def test_malformed_data_never_promotes(self):
        for value in ({},[],True,None):
            result=classify_existing_gpu_context(value)
            self.assertFalse(result["gpu_context_observed"])
            self.assertFalse(result["production_authorized"])
            self.assertEqual(result["decision"],"HOLD")


if __name__=="__main__":
    unittest.main()
