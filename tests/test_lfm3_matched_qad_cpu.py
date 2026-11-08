"""Pure synthetic matched QAD CPU canary gate, never a production permit."""
import copy
import unittest
from research.lfm3_top5.benchmark_report import assess_matched_qad_cpu_bundle

SHA="43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452"

def fixture():
    obs=[]
    for i in range(5):
        obs.append({
            "observation_id":"chatcmpl-synth-"+str(i),
            "finish_reason":"stop","answer":"READY",
            "end_to_end_ms":200.0,
            "usage":{"prompt_tokens":17,"completion_tokens":3,"total_tokens":20},
            "server_timings":{"prompt_n":16,"predicted_n":3,"prompt_ms":150.0,
                "predicted_ms":40.0,"prompt_per_second":100.0,
                "predicted_per_second":50.0},
        })
    return {
        "schema":"lfm3.x1.same-qad.cpu-five.v1",
        "node":"x1-370",
        "model":"LFM3-1.7B-QAD-Q4_0.gguf",
        "expected_sha256":SHA,"observed_sha256":SHA,
        "model_bytes":1049615680,
        "requested_generations":5,"attempted_generations":5,
        "observations":obs,
        "accelerator_verified":False,
        "production_authorized":False,
        "independent_measurement_witness":False,
        "gpu_layers_requested":0,
        "new_service_binding":"127.0.0.1:19183",
        "outcome":"PASS_RESEARCH_ONLY",
        "child_stopped":True,"port_available_after_cleanup":True,
        "mem_available_kib_before":10*1024*1024,
        "mem_available_kib_after":10*1024*1024,
        "memory_psi_some_avg10_before":0.0,
        "memory_psi_some_avg10_after":0.0,
    }

class QadCpuBundleTests(unittest.TestCase):
    def check(self,b=None,digest=SHA):
        return assess_matched_qad_cpu_bundle(fixture() if b is None else b,
                                              expected_checkpoint_sha256=digest)
    def test_valid_matching_report_is_research_only(self):
        r=self.check()
        self.assertEqual(r["decision"],"PASS_RESEARCH_ONLY")
        self.assertEqual(r["observations"],5)
        self.assertEqual(r["server_native_decode_tps_p50"],50)
        self.assertFalse(r["accelerator_verified"])
        self.assertFalse(r["production_authorized"])
    def test_model_hash_mismatch_is_hold(self):
        self.assertEqual(self.check(digest="a"*64)["decision"],"HOLD")
        b=fixture();b["observed_sha256"]="b"*64
        self.assertEqual(self.check(b)["decision"],"HOLD")
    def test_exact_count_and_distinct_ids(self):
        b=fixture();b["observations"][0]["observation_id"]=b["observations"][1]["observation_id"]
        self.assertEqual(self.check(b)["decision"],"HOLD")
        b=fixture();b["attempted_generations"]=6
        self.assertEqual(self.check(b)["decision"],"HOLD")
        b=fixture();b["observations"].append(copy.deepcopy(b["observations"][0]))
        self.assertEqual(self.check(b)["decision"],"HOLD")
    def test_completions_require_exact_usage(self):
        b=fixture();b["observations"][1]["usage"]["total_tokens"]=21
        self.assertEqual(self.check(b)["decision"],"HOLD")
        b=fixture();b["observations"][2]["answer"]="wrong"
        self.assertEqual(self.check(b)["decision"],"HOLD")
        b=fixture();b["observations"][0]["finish_reason"]="length"
        self.assertEqual(self.check(b)["decision"],"HOLD")
    def test_native_rates_are_finite_and_not_derived(self):
        b=fixture();b["observations"][0]["server_timings"]["predicted_per_second"]=float("nan")
        self.assertEqual(self.check(b)["decision"],"HOLD")
        b=fixture();b["observations"][0]["server_timings"]["prompt_per_second"]=0
        self.assertEqual(self.check(b)["decision"],"HOLD")
    def test_cleanup_must_be_confirmed(self):
        for k in ("child_stopped","port_available_after_cleanup"):
            b=fixture();b[k]=False
            self.assertEqual(self.check(b)["decision"],"HOLD")
    def test_no_claimed_acceleration_or_prod(self):
        for k in ("accelerator_verified","production_authorized","independent_measurement_witness"):
            b=fixture();b[k]=True
            self.assertEqual(self.check(b)["decision"],"HOLD")
        b=fixture();b["gpu_layers_requested"]=99
        self.assertEqual(self.check(b)["decision"],"HOLD")
    def test_memory_pressure_and_resource_floor(self):
        for key in ("mem_available_kib_before","mem_available_kib_after"):
            b=fixture();b[key]=1000
            self.assertEqual(self.check(b)["decision"],"HOLD")
        b=fixture();b["memory_psi_some_avg10_after"]=0.21
        self.assertEqual(self.check(b)["decision"],"HOLD")
    def test_wrong_model_port_or_schema(self):
        for key,v in (("model","other.gguf"),("new_service_binding","0.0.0.0:19183"),
                      ("node","unknown"),("schema","untrusted")):
            b=fixture();b[key]=v
            self.assertEqual(self.check(b)["decision"],"HOLD")
    def test_malformed_payload_or_hash_fails_closed(self):
        for val in (None,{},[],True,False):
            x=assess_matched_qad_cpu_bundle(val,expected_checkpoint_sha256=SHA)
            self.assertEqual(x["decision"],"HOLD")
        self.assertEqual(self.check(digest=True)["decision"],"HOLD")

if __name__=="__main__":unittest.main()
