"""Synthetic-only full Deathstar offload depth sweep negative controls."""
import copy
import unittest
from research.lfm3_top5.depth_sweep_audit import (
    assess_depth_report, assess_full_deathstar_sweep, EXPECTED_SHA,
)
from test_lfm3_deathstar_pair_audit import synthetic_pair

DEPTH_NAMES={6:"SIX",8:"EIGHT",10:"TEN"}

def synthetic_sweep():
    cpu,one=synthetic_pair()
    reports={}
    for depth in (6,8,10):
        g=copy.deepcopy(one)
        g.update({
            "schema":f"lfm3.deathstar.vulkan{depth}-five.same-qad.v1",
            "outcome":"PASS_VULKAN_"
                     + DEPTH_NAMES[depth]
                     +"_LAYERS_FIVE_RESEARCH_ONLY",
            "gpu_layers_requested":depth,
            "runtime_reported_output_layer_gpu":True,
            "runtime_reported_assigned_layers":list(range(25-depth,25))*2,
            "server_reported_offload_counts":[
                {"layers":depth,"layers_total":25},
                {"layers":depth,"layers_total":25},
            ],
            "runtime_reported_vulkan_model_buffer_mib":{6:370.26,8:435.64,10:501.03}[depth],
        })
        lines=[]
        for phase in range(2):
            lines.append("I llama_prepare_model_devices: using device Vulkan0 "
                         "(AMD Radeon RX 480 Graphics (RADV POLARIS10)) "
                         "(0000:01:00.0) - 1980 MiB free")
            for layer in range(25-depth,25):
                lines.append(f"D load_tensors: layer {layer} assigned to device Vulkan0, is_swa = 0")
            lines.append("I load_tensors: offloading output layer to GPU")
            lines.append(f"I load_tensors: offloading {depth-1} repeating layers to GPU")
            lines.append(f"I load_tensors: offloaded {depth}/25 layers to GPU")
            buffer=0.0 if phase==0 else {6:370.26,8:435.64,10:501.03}[depth]
            lines.append(f"I load_tensors: Vulkan0 model buffer size = {buffer:.2f} MiB")
            lines.append("I sched_reserve: Vulkan0 compute buffer size = 61.44 MiB")
        g["startup_offload_lines"]=lines
        reports[depth]=g
    return cpu,one,reports

class DepthSweep(unittest.TestCase):
    def audit(self,c=None,g=None,reps=None):
        if c is None and g is None and reps is None:
            c,g,reps=synthetic_sweep()
        return assess_full_deathstar_sweep(
            c,g,reps,expected_checkpoint_sha256=EXPECTED_SHA)

    def test_valid_five_mode_sweep_remains_research_only(self):
        r=self.audit()
        self.assertEqual(r["decision"],"PASS_EXPLORATORY_FIVE_MODE_RESEARCH_ONLY")
        self.assertTrue(r["per_request_gpu_compute_confirmed"])
        self.assertFalse(r["statistical_optimum_established"])
        self.assertFalse(r["production_authorized"])
        self.assertFalse(r["independent_tensor_execution_attested"])

    def test_each_depth_verifies_its_own_exact_counts(self):
        _,_,reps=synthetic_sweep()
        for depth in (6,8,10):
            r=assess_depth_report(reps[depth],depth=depth,
                                  expected_checkpoint_sha256=EXPECTED_SHA)
            self.assertEqual(r["decision"],"PASS_DEPTH_RESEARCH_ONLY")
            self.assertEqual(r["offloaded_layers"],depth)

    def test_missing_depth_report_holds(self):
        c,g,rs=synthetic_sweep();del rs[8]
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")

    def test_expected_depth_mismatch_holds(self):
        _,_,rs=synthetic_sweep()
        self.assertEqual(
            assess_depth_report(rs[6],depth=8,expected_checkpoint_sha256=EXPECTED_SHA)["decision"],
            "HOLD")

    def test_forged_startup_offload_summary_holds(self):
        c,g,rs=synthetic_sweep()
        for i,line in enumerate(rs[8]["startup_offload_lines"]):
            if "offloaded 8/25 layers" in line:
                rs[8]["startup_offload_lines"][i]=line.replace("8/25","7/25")
                break
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")

    def test_misassigned_layer_holds(self):
        c,g,rs=synthetic_sweep()
        rs[10]["runtime_reported_assigned_layers"][0]=12
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")
        c,g,rs=synthetic_sweep()
        rs[10]["startup_offload_lines"][1]="D load_tensors: layer 1 assigned to device Vulkan0"
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")

    def test_no_final_gpu_model_buffer_holds(self):
        c,g,rs=synthetic_sweep()
        rs[6]["runtime_reported_vulkan_model_buffer_mib"]=0
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")

    def test_wrong_gpu_pci_and_client_holds(self):
        c,g,rs=synthetic_sweep()
        rs[8]["startup_offload_lines"][0]="I using device Vulkan0 AMD Radeon RX 480 0000:02:00.0"
        rs[8]["startup_offload_lines"][len(rs[8]["startup_offload_lines"])//2]="I using device Vulkan0 AMD Radeon RX 480 0000:02:00.0"
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")
        c,g,rs=synthetic_sweep()
        rs[8]["observations"][1]["gpu_after"]["amd_clients"][0]["drm-client-id"]="999"
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")

    def test_missing_request_or_duplicate_identifier_holds(self):
        c,g,rs=synthetic_sweep();rs[8]["observations"].pop()
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")
        c,g,rs=synthetic_sweep()
        rs[6]["observations"][1]["id"]=rs[6]["observations"][0]["id"]
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")

    def test_request_counter_replaced_or_replayed_holds(self):
        c,g,rs=synthetic_sweep()
        rs[10]["observations"][3]["compute_delta_ns"]=0
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")
        c,g,rs=synthetic_sweep()
        rs[8]["observations"][4]["gpu_after"]["pid"]+=1
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")
        c,g,rs=synthetic_sweep()
        rs[10]["observations"][4]["gpu_before"]["pid"]+=1
        rs[10]["observations"][4]["gpu_after"]["pid"]+=1
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")

    def test_aggregate_counter_mismatch_holds(self):
        c,g,rs=synthetic_sweep();rs[6]["cumulative_request_compute_delta_ns"]-=1
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")

    def test_pressure_or_cleanup_failure_holds(self):
        c,g,rs=synthetic_sweep()
        rs[8]["capacity_before_request"]["memory_psi_avg10"]=4.16
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")
        c,g,rs=synthetic_sweep()
        rs[8]["port_closed_after_cleanup"]=False
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")

    def test_unsafe_claim_and_bad_tokens_holds(self):
        c,g,rs=synthetic_sweep()
        rs[6]["production_authorized"]=True
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")
        c,g,rs=synthetic_sweep()
        rs[10]["observations"][2]["usage"]["total_tokens"]=99
        self.assertEqual(self.audit(c,g,rs)["decision"],"HOLD")

    def test_malformed_untrusted_data_rejected(self):
        c,g,rs=synthetic_sweep()
        for v in (None,True,[],{}):
            r=self.audit(c,g,v)
            self.assertEqual(r["decision"],"HOLD")
            self.assertFalse(r["production_authorized"])

if __name__=="__main__":unittest.main()
