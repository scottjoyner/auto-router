"""Synthetic-only negative tests for Joyner read-only GPU admission safety."""
import copy
import unittest
from research.lfm3_top5.joyner_gpu_capacity_gate import audit_joyner_readonly

def fixture():
    cap={"MemAvailable_KiB":11*1024*1024,
         "MemTotal_KiB":15*1024*1024,
         "SwapFree_KiB":2*1024*1024,
         "SwapTotal_KiB":4*1024*1024,
         "memory_psi_avg10":0.0,
         "cpu_psi_avg10":0.0,
         "io_psi_avg10":0.0}
    client={"pdev":"0000:03:00.0",
            "driver":"amdgpu",
            "render_device":"/dev/dri/renderD128",
            "client_id":"9999",
            "resident_vram_kib":322560,
            "resident_gtt_kib":1075000,
            "engine_compute_ns":123456789000}
    return {
        "schema":"lfm3.joyner.gpu-capacity-readonly.v1",
        "host":"joyner","uid":1001,
        "read_only":True,"model_requests_sent":0,
        "credentials_accessed":False,"configuration_changed":False,
        "public_ingress_modified":False,"new_models_loaded":False,
        "canonical_ssh_hostkey_enforced":True,
        "source_independently_attested":False,
        "actual_model_sha256_checked_this_session":False,
        "signed_request_gpu_witness":False,
        "rocm_cli_available":False,
        "discrete_gpu":{
            "pci_id":"0x7551","vendor":"0x1002","driver":"amdgpu",
            "pdev":"0000:03:00.0",
            "vram_total":34208743424,
            "vram_used":1024*1024*1024,
            "gtt_total":7920979968,
            "gtt_used":2*1024*1024*1024,
        },
        "samples":[copy.deepcopy(cap),copy.deepcopy(cap)],
        "protected_lfm3":{
            "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
            "model_file_size_bytes":1049615680,
            "host":"127.0.0.1","port":"19175",
            "requested_device":"Vulkan1",
            "requested_gpu_layers":"99",
            "credential_file_flag_present":True,
            "health_http":200,"model_list_http":401,
            "before":{"pid":15551,"process_start_ticks":800000,
                      "gpu_clients":[copy.deepcopy(client)]},
            "after":{"pid":15551,"process_start_ticks":800000,
                     "gpu_clients":[copy.deepcopy(client)]},
        },
    }

class JoynerGPUCapacity(unittest.TestCase):
    def evaluate(self,mutate=None):
        d=fixture()
        if mutate is not None:mutate(d)
        return audit_joyner_readonly(d)

    def test_hardware_context_pass_never_authorizes_any_post(self):
        r=self.evaluate()
        self.assertEqual(r["decision"],"PASS_HARDWARE_CONTEXT_HOLD_NEW_INFERENCE")
        self.assertTrue(r["hardware_and_gpu_context_verified"])
        self.assertTrue(r["existing_protected_service_healthy"])
        self.assertFalse(r["new_model_canary_admitted"])
        self.assertFalse(r["protected_generation_authorized"])
        self.assertFalse(r["production_authorized"])
        self.assertFalse(r["live_gpu_saturation_measured"])

    def test_low_host_ram_and_swap_and_gtt_infer_hold(self):
        def low(d):
            for row in d["samples"]:
                row["MemAvailable_KiB"]=1100000
                row["SwapFree_KiB"]=44
            d["discrete_gpu"]["gtt_used"]=d["discrete_gpu"]["gtt_total"]-1*1024**3
        r=self.evaluate(low)
        self.assertEqual(r["decision"],"PASS_HARDWARE_CONTEXT_HOLD_NEW_INFERENCE")
        self.assertIn("insufficient_system_ram_for_new_model",r["reason_codes"])
        self.assertIn("insufficient_free_swap_for_new_model",r["reason_codes"])
        self.assertIn("insufficient_free_gtt",r["reason_codes"])
        self.assertFalse(r["new_model_canary_admitted"])

    def test_not_even_abundant_resources_release_production(self):
        def ready(d):
            d["rocm_cli_available"]=True
            d["actual_model_sha256_checked_this_session"]=True
            d["signed_request_gpu_witness"]=True
        r=self.evaluate(ready)
        self.assertFalse(r["production_authorized"])
        self.assertFalse(r["new_model_canary_admitted"])
        self.assertFalse(r["protected_generation_authorized"])

    def test_wrong_card_or_igpu_disguised_as_discrete_is_denied(self):
        for field,value in (("pci_id","0x164e"),("pdev","0000:10:00.0"),
                            ("vendor","0x10de"),("driver","nouveau")):
            r=self.evaluate(lambda d:d["discrete_gpu"].__setitem__(field,value))
            self.assertEqual(r["decision"],"HOLD",field)

    def test_wrong_runtime_render_device_denied(self):
        def wrong(d):
            d["protected_lfm3"]["before"]["gpu_clients"][0]["render_device"]="/dev/dri/renderD129"
            d["protected_lfm3"]["after"]["gpu_clients"][0]["render_device"]="/dev/dri/renderD129"
        self.assertEqual(self.evaluate(wrong)["decision"],"HOLD")

    def test_gpu_client_id_drift_denied(self):
        self.assertEqual(self.evaluate(lambda d:
            d["protected_lfm3"]["after"]["gpu_clients"][0].__setitem__("client_id","4004"))["decision"],"HOLD")

    def test_pid_or_start_ticks_drift_denied(self):
        for key in ("pid","process_start_ticks"):
            r=self.evaluate(lambda d:d["protected_lfm3"]["after"].__setitem__(
                key,d["protected_lfm3"]["after"][key]+1))
            self.assertEqual(r["decision"],"HOLD")

    def test_missing_gpu_memory_mapping_denied(self):
        def zero(d):
            for key in ("before","after"):
                c=d["protected_lfm3"][key]["gpu_clients"][0]
                c["resident_gtt_kib"]=0;c["resident_vram_kib"]=0
        self.assertEqual(self.evaluate(zero)["decision"],"HOLD")

    def test_stale_compute_counter_goes_no_further_than_context(self):
        r=self.evaluate()
        self.assertFalse(r["independent_gpu_token_execution_attested"])
        self.assertIn("live_gpu_utilization_and_speed_not_measured",r["reason_codes"])

    def test_nonmonotonic_gpu_engine_denied(self):
        r=self.evaluate(lambda d:
            d["protected_lfm3"]["after"]["gpu_clients"][0].__setitem__("engine_compute_ns",100))
        self.assertEqual(r["decision"],"HOLD")

    def test_swap_free_more_than_total_denied(self):
        r=self.evaluate(lambda d:d["samples"][0].__setitem__("SwapFree_KiB",5*1024*1024))
        self.assertEqual(r["decision"],"HOLD")

    def test_wrong_protected_service_identity_denied(self):
        for key,value in (("port","19176"),("model_basename","other.gguf"),
                          ("requested_device","none"),("requested_gpu_layers","0"),
                          ("credential_file_flag_present",False),
                          ("health_http",503),("model_list_http",200)):
            r=self.evaluate(lambda d:d["protected_lfm3"].__setitem__(key,value))
            self.assertEqual(r["decision"],"HOLD",key)

    def test_reject_unverified_host_key_or_any_credential_access(self):
        for field,value in (("canonical_ssh_hostkey_enforced",False),
                            ("credentials_accessed",True),
                            ("model_requests_sent",1),
                            ("configuration_changed",True),
                            ("new_models_loaded",True),
                            ("public_ingress_modified",True)):
            r=self.evaluate(lambda d:d.__setitem__(field,value))
            self.assertEqual(r["decision"],"HOLD",field)

    def test_missing_sample_and_invalid_psi_denied(self):
        self.assertEqual(self.evaluate(lambda d:d["samples"].pop())["decision"],"HOLD")
        self.assertEqual(self.evaluate(lambda d:d["samples"][0].__setitem__(
            "memory_psi_avg10",float("nan")))["decision"],"HOLD")

    def test_missing_gpu_memory_and_overuse_denied(self):
        self.assertEqual(self.evaluate(lambda d:d["discrete_gpu"].__setitem__(
            "gtt_used",d["discrete_gpu"]["gtt_total"]+1))["decision"],"HOLD")
        self.assertEqual(self.evaluate(lambda d:d["discrete_gpu"].pop("vram_total"))["decision"],"HOLD")

    def test_invalid_source_types_denied(self):
        for item in (None,[],True,{},{"schema":"wrong"}):
            r=audit_joyner_readonly(item)
            self.assertEqual(r["decision"],"HOLD")
            self.assertFalse(r["production_authorized"])

if __name__=="__main__":unittest.main()
