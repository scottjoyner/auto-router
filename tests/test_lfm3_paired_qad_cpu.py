"""Synthetic x1-370 / xwing QAD CPU comparison with alias-normalized workload."""
import copy,hashlib,json,unittest
from research.lfm3_top5.benchmark_report import assess_paired_qad_cpu_bundles

SHA="43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452"

def workload_hash(alias):
    body={"model":alias,
          "messages":[{"role":"user","content":"Respond with exactly READY."}],
          "temperature":0,"max_tokens":24,"stream":False,
          "chat_template_kwargs":{"enable_thinking":False}}
    raw=json.dumps(body,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()
    return hashlib.sha256(raw).hexdigest()

def fixture():
    rows=[]
    for i in range(5):
        rows.append({
            "observation_id":"chatcmpl-synthetic-"+str(i),
            "finish_reason":"stop","answer":"READY","end_to_end_ms":200.0,
            "usage":{"prompt_tokens":17,"completion_tokens":3,"total_tokens":20},
            "server_timings":{"prompt_n":16,"predicted_n":3,"prompt_ms":150.0,
                              "predicted_ms":40.0,"prompt_per_second":100.0,
                              "predicted_per_second":50.0}})
    x1={
        "schema":"lfm3.x1.same-qad.cpu-five.v1","node":"x1-370",
        "model":"LFM3-1.7B-QAD-Q4_0.gguf","expected_sha256":SHA,
        "observed_sha256":SHA,"model_bytes":1049615680,
        "gpu_layers_requested":0,"new_service_binding":"127.0.0.1:19183",
        "requested_generations":5,"attempted_generations":5,
        "observations":copy.deepcopy(rows),"outcome":"PASS_RESEARCH_ONLY",
        "child_stopped":True,"port_available_after_cleanup":True,
        "accelerator_verified":False,"production_authorized":False,
        "independent_measurement_witness":False,
        "mem_available_kib_before":10*1024*1024,
        "mem_available_kib_after":10*1024*1024,
        "memory_psi_some_avg10_before":0.0,
        "memory_psi_some_avg10_after":0.0,
        "workload_sha256":workload_hash("lfm3-qad-x1-cpu-research"),
    }
    wing={
        "schema":"lfm3.xwing.same-qad.cpu-five.v2","node":"xwing",
        "model":"LFM3-1.7B-QAD-Q4_0.gguf","expected_sha256":SHA,
        "observed_sha256":SHA,"model_bytes":1049615680,
        "gpu_layers_requested":0,"offload_device_selection":"none",
        "new_service_binding":"127.0.0.1:19183",
        "requested_generations":5,"attempted_generations":5,
        "observations":copy.deepcopy(rows),"decision":"PASS_RESEARCH_ONLY",
        "temporary_child_stopped":True,"temporary_log_removed":True,
        "port_closed_after_cleanup":True,
        "accelerator_verified":False,"production_authorized":False,
        "independent_measurement_witness":False,
        "credentials_read":False,"existing_services_modified":False,
        "memory_before":{"MemAvailable":6*1024*1024,"SwapFree":10*1024*1024},
        "memory_after":{"MemAvailable":6*1024*1024,"SwapFree":10*1024*1024},
        "psi_before":{"memory":0.0,"cpu":0.1},
        "psi_after":{"memory":0.0,"cpu":0.1},
        "workload_sha256":workload_hash("lfm3-qad-xwing-cpu-research"),
    }
    return x1,wing

class PairedQadCPU(unittest.TestCase):
    def check(self,a=None,b=None,digest=SHA):
        if a is None and b is None:a,b=fixture()
        return assess_paired_qad_cpu_bundles(a,b,expected_checkpoint_sha256=digest)

    def test_bounded_pair_matches_after_alias_normalization(self):
        result=self.check()
        self.assertEqual(result["decision"],"PASS_RESEARCH_ONLY")
        self.assertTrue(result["same_checkpoint_bytes_reported"])
        self.assertTrue(result["same_normalized_workload"])
        self.assertEqual(result["per_node_count"],5)
        self.assertEqual(result["cpu_decode_tps_p50"]["x1-370"],50)
        self.assertEqual(result["cpu_decode_tps_p50"]["xwing"],50)
        self.assertFalse(result["accelerator_verified"])
        self.assertFalse(result["production_authorized"])

    def test_raw_hashes_differ_for_distinct_server_aliases(self):
        a,b=fixture()
        self.assertNotEqual(a["workload_sha256"],b["workload_sha256"])
        b["workload_sha256"]=a["workload_sha256"]
        self.assertEqual(self.check(a,b)["decision"],"HOLD")

    def test_different_checkpoint_or_alias_blocks_pair(self):
        a,b=fixture()
        b["observed_sha256"]="a"*64
        self.assertEqual(self.check(a,b)["decision"],"HOLD")
        self.assertEqual(self.check(digest="b"*64)["decision"],"HOLD")

    def test_xwing_gpu_offload_or_prod_claim_blocks_pair(self):
        for key,val in (("gpu_layers_requested",99),
                        ("offload_device_selection","0"),
                        ("accelerator_verified",True),
                        ("production_authorized",True)):
            a,b=fixture()
            b[key]=val
            self.assertEqual(self.check(a,b)["decision"],"HOLD")

    def test_missing_cleanup_or_privacy_block_pair(self):
        for key,val in (("temporary_child_stopped",False),
                        ("temporary_log_removed",False),
                        ("port_closed_after_cleanup",False),
                        ("credentials_read",True),
                        ("existing_services_modified",True)):
            a,b=fixture();b[key]=val
            self.assertEqual(self.check(a,b)["decision"],"HOLD")

    def test_xwing_unchanged_pressure_and_sufficient_ram(self):
        a,b=fixture()
        b["memory_after"]["MemAvailable"]=1000
        self.assertEqual(self.check(a,b)["decision"],"HOLD")
        a,b=fixture()
        b["psi_after"]["memory"]=float("nan")
        self.assertEqual(self.check(a,b)["decision"],"HOLD")

    def test_responses_unique_complete_and_bounded(self):
        a,b=fixture();b["observations"][1]["observation_id"]=b["observations"][0]["observation_id"]
        self.assertEqual(self.check(a,b)["decision"],"HOLD")
        a,b=fixture();b["observations"][3]["usage"]["total_tokens"]=21
        self.assertEqual(self.check(a,b)["decision"],"HOLD")
        a,b=fixture();b["attempted_generations"]=6
        self.assertEqual(self.check(a,b)["decision"],"HOLD")

    def test_wrong_native_timing_not_promoted(self):
        a,b=fixture();b["observations"][2]["server_timings"]["predicted_per_second"]=float("nan")
        self.assertEqual(self.check(a,b)["decision"],"HOLD")

    def test_wrong_model_config_or_schema_not_promoted(self):
        for key,value in (("model","wrong.gguf"),("schema","untrusted"),
                          ("new_service_binding","0.0.0.0:19183")):
            a,b=fixture();b[key]=value
            self.assertEqual(self.check(a,b)["decision"],"HOLD")

    def test_malformed_and_missing_input_fail_closed(self):
        a,b=fixture()
        for val in ({},None,[],True):
            self.assertEqual(self.check(val,b)["decision"],"HOLD")
            self.assertEqual(self.check(a,val)["decision"],"HOLD")

if __name__=="__main__":unittest.main()
