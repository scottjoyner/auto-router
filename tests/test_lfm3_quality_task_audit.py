"""Frozen fully synthetic research quality-matrix adversarial cases."""
import copy,hashlib,json,unittest
from research.lfm3_top5.quality_task_audit import (
 PIN,SEED,SOURCE_SHA,ORACLES,order,canonical,exact_task_oracle,
 assess_block,assess_full_matrix,
)

def sample(*,correct=True):
 sequence=order()
 prereg={
  "schema":"lfm3.deathstar.quality-random-preregistered.v1",
  "host":"deathstar-XPS-8920","seed":SEED,
  "source_script_sha256":SOURCE_SHA,
  "model_sha256":PIN,"target_total_requests":24,
  "production_authorized":False,
  "independent_tensor_execution_attested":False,
  "task_oracles_sha256":{
   name:hashlib.sha256(canonical(o["expected"]).encode()).hexdigest()
   for name,o in ORACLES.items()
  },
  "order":[{"mode":m,"block":b,"task":t} for m,b,t in sequence],
 }
 blocks={}
 cap={
  "MemAvailable_KiB":17*1024*1024,"SwapFree_KiB":100*1024*1024,
  "memory_psi_avg10":0.2,"cpu_psi_avg10":0.1,"io_psi_avg10":0.0,
  "gpu_busy_percent":0,
  "mem_info_vram_total":8*1024**3,
  "mem_info_vram_used":6*1024**3,
 }
 for index,(mode,block,task) in enumerate(sequence):
  layers={"cpu":0,"vulkan6":6,"vulkan8":8}[mode]
  if correct:
   answer=canonical(ORACLES[task]["expected"])
  else:
   answer=canonical({"noncanonical":"inference_completed_but_not_correct"})
  rows=[]
  for i in range(2):
   rows.append({
    "id":f"synthetic-{mode}-{block}-{task}-{i}",
    "finish_reason":"stop",
    "response_sha256":hashlib.sha256(answer.encode()).hexdigest(),
    "full_public_synthetic_answer":answer,
    "response_first_100_chars":answer[:100],
    "semantic_oracle_pass":correct,
    "usage":{"prompt_tokens":44,"completion_tokens":55,"total_tokens":99},
    "timings":{"predicted_n":55,
        "prompt_ms":105.0,"predicted_ms":2200.0,
        "prompt_per_second":100.0,
        "predicted_per_second":25.0},
    "wall_ms":2450.0,
    "pid":28000+index,
    "process_start_ticks":1000000+index*100,
    "drm_client":str(20000+index) if layers else "1",
    "drm_compute_delta_ns":115000000+i*10000 if layers else None,
    "pre_pressure":0.0,"post_pressure":0.1,
    "post_vram_free_mib":1700.0,
   })
  blocks[(mode,block,task)]={
   "schema":"lfm3.deathstar.quality-task-block-v1",
   "host":"deathstar-XPS-8920",
   "mode":mode,"block":block,"workload":task,
   "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
   "model_sha256_expected":PIN,"model_sha256_observed":PIN,
   "offloaded_layers_requested":layers,
   "device":"none" if not layers else "Vulkan0",
   "port":"127.0.0.1:19190",
   "alias":"lfm3-deathstar-quality-research",
   "requests_requested":2,"requests_attempted":2,
   "max_tokens":160,
   "prompt_sha256":ORACLES[task]["prompt_sha"],
   "request_sha256":ORACLES[task]["request_sha"],
   "outcome":"PASS_RESEARCH_CAPTURE_WITH_QUALITY_SCORES",
   "credentials_read":False,"existing_services_modified":False,
   "public_ingress_enabled":False,"production_authorized":False,
   "independent_tensor_execution_attested":False,
   "cpu_mode_gpu_compute_counter_verified":False,
   "temporary_child_stopped":True,
   "temporary_log_removed":True,"temporary_port_closed":True,
   "preflight":copy.deepcopy(cap),
   "post_hash":copy.deepcopy(cap),
   "capacity_after_cleanup":copy.deepcopy(cap),
   "cooldown":[{"seconds":float(n),"memory_psi":0.1,"gpu_busy":0}
      for n in range(3)],
   "runtime_offload":{
    "summaries":[[layers,25]]*2 if layers else [],
    "assigned_layers":list(range(25-layers,25))*2 if layers else [],
    "final_vulkan_model_buffer_mib":{0:None,6:370.26,8:435.64}[layers],
   },
   "oracle_pass_count":2 if correct else 0,
   "requests":rows,
  }
 return prereg,blocks

class QualityTaskAudit(unittest.TestCase):
 def test_positive_synthetic_all_true_is_research_only(self):
  m,rs=sample()
  r=assess_full_matrix(m,rs)
  self.assertEqual(r["decision"],"PASS_SAFE_CAPTURE_WITH_EXACT_QUALITY_GATE_RESEARCH_ONLY")
  self.assertEqual(r["quality_pass_count"],24)
  self.assertEqual(r["generated_responses_verified"],24)
  self.assertFalse(r["production_authorized"])
  self.assertFalse(r["externally_attested_gpu_tensor_kernels"])

 def test_all_incorrect_quality_can_be_valid_capture(self):
  m,rs=sample(correct=False)
  r=assess_full_matrix(m,rs)
  self.assertEqual(r["decision"],"PASS_SAFE_CAPTURE_WITH_EXACT_QUALITY_GATE_RESEARCH_ONLY")
  self.assertEqual(r["quality_pass_count"],0)
  self.assertEqual(r["per_mode"]["cpu"]["exact_oracle_passes"],0)
  self.assertFalse(r["production_authorized"])

 def test_correct_and_incorrect_answer_scoring(self):
  self.assertTrue(exact_task_oracle("gate",canonical(ORACLES["gate"]["expected"])))
  self.assertFalse(exact_task_oracle("gate",canonical({"decision":"PASS"})))
  self.assertFalse(exact_task_oracle("nodes",'{"total":NaN}'))

 def test_answer_hash_forgery_denied(self):
  m,rs=sample()
  rs[("cpu",1,"gate")]["requests"][0]["response_sha256"]="a"*64
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_answer_prefix_forgery_denied(self):
  m,rs=sample()
  rs[("vulkan6",1,"gate")]["requests"][0]["response_first_100_chars"]="not the answer"
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_reported_quality_not_accepted_without_oracle(self):
  m,rs=sample(correct=False)
  rs[("vulkan8",1,"nodes")]["requests"][0]["semantic_oracle_pass"]=True
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_aggregate_quality_count_mismatch_denied(self):
  m,rs=sample()
  rs[("cpu",2,"nodes")]["oracle_pass_count"]=1
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_wrong_model_or_task_hash_denied(self):
  m,rs=sample()
  rs[("vulkan8",2,"nodes")]["model_sha256_observed"]="a"*64
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")
  m,rs=sample()
  rs[("cpu",1,"gate")]["request_sha256"]="a"*64
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_reordered_preregistration_rejected(self):
  m,rs=sample()
  m["order"][0],m["order"][1]=m["order"][1],m["order"][0]
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_oracle_commitment_and_source_hash_cannot_change(self):
  m,rs=sample()
  m["task_oracles_sha256"]["gate"]="a"*64
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")
  m,rs=sample()
  m["source_script_sha256"]="b"*64
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_incomplete_blocks_denied(self):
  m,rs=sample()
  rs.pop(("vulkan8",2,"gate"))
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_missing_request_or_duplicate_id_denied(self):
  m,rs=sample()
  rs[("cpu",1,"gate")]["requests"].pop()
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")
  m,rs=sample()
  rs[("vulkan6",2,"nodes")]["requests"][1]["id"]=rs[("vulkan6",2,"nodes")]["requests"][0]["id"]
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_reused_process_across_blocks_denied(self):
  m,rs=sample()
  source=rs[("cpu",1,"gate")]["requests"][0]
  for row in rs[("cpu",2,"gate")]["requests"]:
   row["pid"]=source["pid"]
   row["process_start_ticks"]=source["process_start_ticks"]
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_wrong_gpu_offload_or_client_denied(self):
  m,rs=sample()
  rs[("vulkan8",2,"gate")]["runtime_offload"]["summaries"][1]=[6,25]
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")
  m,rs=sample()
  rs[("vulkan6",1,"nodes")]["requests"][1]["drm_client"]="9999"
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_missing_gpu_activity_or_cpu_positive_activity_denied(self):
  m,rs=sample()
  rs[("vulkan6",1,"gate")]["requests"][0]["drm_compute_delta_ns"]=0
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")
  m,rs=sample()
  rs[("cpu",1,"nodes")]["requests"][0]["drm_compute_delta_ns"]=10000
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_unsafe_psi_and_gpu_headroom_denied(self):
  m,rs=sample()
  rs[("cpu",1,"gate")]["preflight"]["memory_psi_avg10"]=4.16
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")
  m,rs=sample()
  rs[("vulkan8",2,"nodes")]["requests"][0]["post_vram_free_mib"]=100
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_failed_cleanup_denied(self):
  m,rs=sample()
  rs[("vulkan8",2,"nodes")]["temporary_port_closed"]=False
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_forced_credentials_or_production_denied(self):
  m,rs=sample()
  rs[("cpu",2,"nodes")]["credentials_read"]=True
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")
  m,rs=sample()
  rs[("vulkan8",2,"nodes")]["production_authorized"]=True
  self.assertEqual(assess_full_matrix(m,rs)["decision"],"HOLD")

 def test_untrusted_inputs_fail_closed(self):
  m,rs=sample()
  for bad in (None,[],True,{}):
   self.assertEqual(assess_full_matrix(bad,rs)["decision"],"HOLD")
   self.assertEqual(assess_full_matrix(m,bad)["decision"],"HOLD")

if __name__=="__main__":unittest.main()
