"""Frozen synthetic-only GPU capacity triage; never authorizes workloads."""
import copy
import unittest
from research.lfm3_top5.gpu_canary_gate import assess_gpu_canary_capacity


def sample(host):
    mem={"MemAvailable":10*1024*1024,"SwapFree":2*1024*1024,"SwapTotal":4*1024*1024}
    card={"card":"card1","gpu_busy_percent":10,
          "mem_info_vram_total":2*1024**3,
          "mem_info_vram_used":1024**3}
    amd={"gpu":0,"gfx":{"unit":"%","value":10},
         "vram_free":{"unit":"MB","value":1024}}
    if host=="xwing":
        # gfx1151 UMA sysfs 512MiB aperture is not amd-smi's full
        # 24GiB GPU-addressable shared memory pool.
        card.update({"mem_info_vram_total":512*1024**2,
                     "mem_info_vram_used":488*1024**2})
        amd["vram_free"]["value"]=22000
    if host=="joyner":
        card={"card":"card0","mem_info_vram_total":32*1024**3,
              "mem_info_vram_used":2*1024**3}
        memory_gpu={"card":"card1","gpu_busy_percent":5,
                    "mem_info_vram_total":512*1024**2,
                    "mem_info_vram_used":50*1024**2}
        gpu=[card,memory_gpu]
    else:
        gpu=[card]
    return {"hostname":host,"read_only":True,"model_loads":0,
            "credentials_read":False,"generation_requests":0,
            "host_config_changes":False,"memory_kib":mem,
            "memory_psi_avg10":0.0,"server_count":1,"gpu":gpu,
            "amd_smi":[amd]}


class Admission(unittest.TestCase):
    def check(self,data=None,host="xwing"):
        return assess_gpu_canary_capacity(sample(host) if data is None else data,host)

    def test_uma_gpu_has_shared_headroom_but_still_no_auto_admission(self):
        x=self.check()
        self.assertTrue(x["capacity_candidate"])
        self.assertNotIn("gpu_vram_headroom_insufficient",x["reasons"])
        self.assertEqual(x["decision"],"HOLD")
        self.assertFalse(x["gpu_token_execution_verified"])
        self.assertFalse(x["production_authorized"])
        self.assertFalse(x["operator_authorization_verified"])

    def test_xwing_bustling_server_count_and_gfx100_held(self):
        d=sample("xwing");d["server_count"]=3;d["gpu"][0]["gpu_busy_percent"]=100
        d["amd_smi"][0]["gfx"]["value"]=100
        x=self.check(d)
        self.assertFalse(x["capacity_candidate"])
        self.assertIn("gpu_contention_or_busy_unknown",x["reasons"])
        self.assertIn("existing_xwing_model_workload_contention",x["reasons"])

    def test_x1_scant_vram_and_invalid_amd_gfx_held(self):
        d=sample("x1-370")
        d["gpu"][0]["mem_info_vram_used"]=int(1.95*1024**3)
        d["amd_smi"][0]["gfx"]="N/A"
        x=self.check(d,"x1-370")
        self.assertFalse(x["capacity_candidate"])
        self.assertIn("gpu_vram_headroom_insufficient",x["reasons"])
        self.assertIn("amd_smi_busy_or_unavailable",x["reasons"])

    def test_joyner_discrete_card_selection_and_protected_auth(self):
        x=self.check(sample("joyner"),"joyner")
        self.assertNotIn("kernel_gpu_telemetry_unverified",x["reasons"])
        self.assertIn("protected_joyner_existing_service_requires_approved_client",x["reasons"])
        self.assertFalse(x["capacity_candidate"])

    def test_low_ram_and_swap_stop_even_if_gpu_idle(self):
        d=sample("xwing")
        d["memory_kib"]["MemAvailable"]=700
        d["memory_kib"]["SwapFree"]=0
        x=self.check(d)
        self.assertIn("low_or_unknown_ram_headroom",x["reasons"])
        self.assertIn("low_or_unknown_swap_headroom",x["reasons"])

    def test_missing_gfx_and_malformed_busy_hold(self):
        for value in ("N/A",None,True,float("nan"),-1,100):
            d=sample("xwing");d["amd_smi"][0]["gfx"]=value
            self.assertFalse(self.check(d)["capacity_candidate"])

    def test_spoofed_operator_claim_cannot_authorize(self):
        d=sample("xwing")
        d.update({"operator_authorized":True,"gpu_token_execution_verified":True,
                  "production_authorized":True,"signed_by_independent_witness":True})
        x=self.check(d)
        self.assertEqual(x["decision"],"HOLD")
        self.assertFalse(x["production_authorized"])
        self.assertFalse(x["operator_authorization_verified"])

    def test_host_and_collection_mutation_not_eligible(self):
        for key,value in (("hostname","other"),("read_only",False),
                          ("credentials_read",True),("model_loads",1),
                          ("generation_requests",1),("host_config_changes",True)):
            d=sample("xwing");d[key]=value
            x=self.check(d)
            self.assertFalse(x["capacity_candidate"])

    def test_malformed_dicts_fail_closed(self):
        for x in (None,[],True,{},{"hostname":"xwing"}):
            r=assess_gpu_canary_capacity(x,"xwing")
            self.assertEqual(r["decision"],"HOLD")
            self.assertFalse(r["capacity_candidate"])
        self.assertFalse(assess_gpu_canary_capacity(sample("xwing"),"invalid")["capacity_candidate"])


if __name__=="__main__":unittest.main()
