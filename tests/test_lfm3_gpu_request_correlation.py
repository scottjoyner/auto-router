"""Synthetic-only GPU/request correlation. Nothing here proves key custody."""
import copy
import unittest

from research.lfm3_top5.benchmark_report import assess_gpu_request_correlation

DIGEST="a"*64

def fixture():
    a={"pid":2222,"process_starttime_ticks":12345678,"model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
       "alias":"lfm3-1.7b-research","port":"19175","host":"127.0.0.1",
       "gpu_layers_requested":"99","drm_clients":[{
           "device":"/dev/dri/renderD128","metrics":{
               "drm-driver":"amdgpu","drm-pdev":"0000:03:00.0",
               "drm-client-id":"123","drm-resident-vram":"5859 MiB",
               "drm-engine-compute":"200000000 ns"}}]}
    b=copy.deepcopy(a)
    b["drm_clients"][0]["metrics"]["drm-engine-compute"]="201000000 ns"
    return {"context":{
                "schema":"lfm3.joyner.gpu-process.read-only.v1","hostname":"joyner",
                "before":a,"after":b,
                "http_status_no_credentials":{"/health":200,"/v1/models":401},
                "backend_dispatch_correlated_to_tokens":False,
                "authenticated_generation_receipt":False,
                "model_checkpoint_sha256_verified":False,
                "independent_gpu_execution_witness":False,
                "production_authorized":False,
            },
            "request":{
                "schema":"lfm3.joyner.one-request.correlation.v1",
                "model":"lfm3-1.7b-research",
                "checkpoint_sha256":DIGEST,
                "server_request_id":"chatcmpl-synthetic-001",
                "server_pid":2222,"process_starttime_ticks":12345678,
                "collector_before_ns":1_000_000_000,
                "request_started_ns":1_100_000_000,
                "request_finished_ns":1_300_000_000,
                "collector_after_ns":1_500_000_000,
                "generations_attempted":1,"requested_max_tokens":24,
                "finish_reason":"stop","response_text":"READY",
                "usage":{"prompt_tokens":17,"completion_tokens":3,"total_tokens":20},
                "credentials_retained":False,
                "externally_authenticated_witness":False,
            }}

class CorrelationContract(unittest.TestCase):
    def check(self,bundle=None,expected=DIGEST):
        return assess_gpu_request_correlation(
            fixture() if bundle is None else bundle,
            expected_checkpoint_sha256=expected)

    def test_correlated_synthetic_activity_is_only_a_candidate(self):
        x=self.check()
        self.assertEqual(x["decision"],"HOLD")
        self.assertTrue(x["gpu_context_observed"])
        self.assertTrue(x["bounded_request_contract_satisfied"])
        self.assertTrue(x["temporal_gpu_activity_candidate"])
        self.assertEqual(x["compute_delta_ns"],1_000_000)
        self.assertFalse(x["accelerator_verified"])
        self.assertFalse(x["production_authorized"])

    def test_no_gpu_compute_change_cannot_correlate(self):
        b=fixture()
        b["context"]["after"]["drm_clients"][0]["metrics"]["drm-engine-compute"]="200000000 ns"
        self.assertFalse(self.check(b)["temporal_gpu_activity_candidate"])

    def test_process_replacement_or_id_mismatch_rejected(self):
        b=fixture();b["request"]["server_pid"]+=1
        self.assertFalse(self.check(b)["bounded_request_contract_satisfied"])
        b=fixture();b["context"]["after"]["process_starttime_ticks"]+=1
        self.assertFalse(self.check(b)["bounded_request_contract_satisfied"])

    def test_request_outside_sampling_window_rejected(self):
        for field,value in (("request_started_ns",500_000_000),
                            ("request_finished_ns",1_600_000_000),
                            ("collector_after_ns",1_000_000_000)):
            b=fixture();b["request"][field]=value
            self.assertFalse(self.check(b)["temporal_gpu_activity_candidate"])

    def test_too_long_or_retried_generation_rejected(self):
        b=fixture();b["request"]["collector_after_ns"]=70_000_000_000
        self.assertFalse(self.check(b)["temporal_gpu_activity_candidate"])
        b=fixture();b["request"]["generations_attempted"]=2
        self.assertFalse(self.check(b)["bounded_request_contract_satisfied"])

    def test_mismatched_checkpoint_is_not_a_candidate(self):
        self.assertFalse(self.check(expected="b"*64)["bounded_request_contract_satisfied"])
        self.assertFalse(self.check(expected="bad")["bounded_request_contract_satisfied"])
        b=fixture();b["request"]["checkpoint_sha256"]="c"*64
        self.assertFalse(self.check(b)["bounded_request_contract_satisfied"])

    def test_model_and_token_accounting_must_match(self):
        b=fixture();b["request"]["model"]="other"
        self.assertFalse(self.check(b)["bounded_request_contract_satisfied"])
        b=fixture();b["request"]["usage"]["total_tokens"]=22
        self.assertFalse(self.check(b)["bounded_request_contract_satisfied"])

    def test_missing_or_ambiguous_receipt_rejected(self):
        b=fixture();b["request"]["server_request_id"]=""
        self.assertFalse(self.check(b)["bounded_request_contract_satisfied"])
        b=fixture();b["request"]["finish_reason"]="length"
        self.assertFalse(self.check(b)["bounded_request_contract_satisfied"])
        b=fixture();b["request"]["response_text"]="WRONG"
        self.assertFalse(self.check(b)["bounded_request_contract_satisfied"])

    def test_claimed_external_witness_cannot_self_authorize(self):
        b=fixture();b["request"]["externally_authenticated_witness"]=True
        x=self.check(b)
        self.assertEqual(x["decision"],"HOLD")
        self.assertFalse(x["accelerator_verified"])
        self.assertFalse(x["production_authorized"])
        b=fixture();b["request"]["credentials_retained"]=True
        self.assertFalse(self.check(b)["bounded_request_contract_satisfied"])

    def test_malformed_or_missing_payload_fail_closed(self):
        for b in (None,{},[],True,{"context":{},"request":{}},
                  {"context":[],"request":[]}):
            r=assess_gpu_request_correlation(b,expected_checkpoint_sha256=DIGEST)
            self.assertFalse(r["bounded_request_contract_satisfied"])
            self.assertFalse(r["accelerator_verified"])
            self.assertFalse(r["production_authorized"])
            self.assertEqual(r["decision"],"HOLD")

if __name__=="__main__":
    unittest.main()
