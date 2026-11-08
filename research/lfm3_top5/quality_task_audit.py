"""Pure audit for preregistered synthetic fleet-ops JSON tasks on LFM3 RX480.

Always distinguish infrastructure/request PASS from exact output-quality
PASS. A model can decode at high speed and still deliver unusable release
gates. Operator-local receipts are not independent GPU kernel attestation.
This code is deploy-inert, operates on synthetic records and always denies
authorization of production, statistical superiority, and external custody.
"""
from __future__ import annotations
import hashlib,json,math,random,re
from statistics import median

PIN="43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452"
SEED=202611081402
SOURCE_SHA="28451e5d8450a4ed33c3dc3963fe3087827e26bc9070f38c63ff9698165f1fec"
MODES=("cpu","vulkan6","vulkan8")
GROUPS=(("gate",1),("nodes",1),("nodes",2),("gate",2))
ORACLES={
  "gate":{
    "expected":{"decision":"HOLD","failed_gates":["database","signer","tls"],"passed_gates":["ci","source"]},
    "prompt_sha":"a7e460370412d5873658ab3bc4c3e033236e14277e667393b881f9b1ad73e285",
    "request_sha":"72e5f87c5d4420d12e1ff466e835c3d23936479cc8b09e45057d5483c5b4673a",
  },
  "nodes":{
    "expected":{"hold_or_fail":["joyner","optiplex","xwing"],"healthy":["deathstar","destroyer","x1-370"],"total":6},
    "prompt_sha":"774b3ed73671aaf7b8e27996c46716adeffa68bcd52b64308d57b76fe07149f4",
    "request_sha":"b16cb2d5d6fea91b3ab7e30e4eab54ade5706010b167293dada6e12691c4fee4",
  },
}
def order():
 rng=random.Random(SEED)
 return [(mode,block,task) for task,block in GROUPS
         for mode in rng.sample(MODES,k=len(MODES))]

def _hold(reason):
 return {"decision":"HOLD","reason":reason,"quality_pass_count":None,
         "runtime_blocks_verified":0,"generated_responses_verified":0,
         "externally_attested_gpu_tensor_kernels":False,
         "statistical_superiority_established":False,
         "production_authorized":False}

def canonical(obj):
 return json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=True)

def _unique_json_pairs(pairs):
 """Reject duplicate JSON object keys at any nesting depth."""
 output={}
 for key,value in pairs:
  if key in output:raise ValueError("duplicate_json_key")
  output[key]=value
 return output

def _reject_json_constant(_value):
 raise ValueError("non_finite_json_constant")

def exact_task_oracle(task,answer):
 """Fail-closed whole-object JSON oracle; never trust reported model score."""
 if task not in ORACLES or type(answer) is not str or not 1<=len(answer)<=2000:
  return False
 if "<think>" in answer.lower() or "</think>" in answer.lower():
  return False
 try:
  obj=json.loads(answer,object_pairs_hook=_unique_json_pairs,
                 parse_constant=_reject_json_constant)
 except (TypeError,ValueError,RecursionError):
  return False
 if type(obj) is not dict:return False
 return canonical(obj)==canonical(ORACLES[task]["expected"])

def capacity(m):
 if type(m) is not dict:return False
 for key,lower in (("MemAvailable_KiB",10*1024*1024),
                   ("SwapFree_KiB",1024*1024)):
  if type(m.get(key)) is not int or m[key]<lower:return False
 for key,limit in (("memory_psi_avg10",1),("cpu_psi_avg10",2),("io_psi_avg10",5)):
  value=m.get(key)
  if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=limit:return False
 total=m.get("mem_info_vram_total")
 used=m.get("mem_info_vram_used")
 busy=m.get("gpu_busy_percent")
 return (type(total) is int and type(used) is int and 0<=used<=total
         and total-used>=1536*1024*1024
         and type(busy) is int and 0<=busy<=10)

def assess_block(receipt,mode,block,task):
 if (mode not in MODES or block not in (1,2) or task not in ORACLES):
  return _hold("unrecognized_mode_or_task")
 if type(receipt) is not dict:
  return _hold("missing_receipt")
 layers={"cpu":0,"vulkan6":6,"vulkan8":8}[mode]
 spec=ORACLES[task]
 expected={
  "schema":"lfm3.deathstar.quality-task-block-v1",
  "host":"deathstar-XPS-8920",
  "mode":mode,"block":block,"workload":task,
  "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
  "model_sha256_expected":PIN,"model_sha256_observed":PIN,
  "offloaded_layers_requested":layers,
  "device":"none" if layers==0 else "Vulkan0",
  "port":"127.0.0.1:19190",
  "alias":"lfm3-deathstar-quality-research",
  "requests_requested":2,"requests_attempted":2,
  "max_tokens":160,
  "prompt_sha256":spec["prompt_sha"],
  "request_sha256":spec["request_sha"],
  "outcome":"PASS_RESEARCH_CAPTURE_WITH_QUALITY_SCORES",
  "credentials_read":False,"existing_services_modified":False,
  "public_ingress_enabled":False,
  "production_authorized":False,
  "independent_tensor_execution_attested":False,
  "cpu_mode_gpu_compute_counter_verified":False,
  "temporary_child_stopped":True,
  "temporary_log_removed":True,"temporary_port_closed":True,
 }
 for key,want in expected.items():
  if type(receipt.get(key)) is not type(want) or receipt.get(key)!=want:
   return _hold("identity_or_boundary_"+key)
 for k in ("preflight","post_hash","capacity_after_cleanup"):
  if not capacity(receipt.get(k)):
   return _hold("resource_"+k)
 cooldown=receipt.get("cooldown")
 if type(cooldown) is not list or not 3<=len(cooldown)<=18:
  return _hold("no_load_cooldown")
 for item in cooldown:
  if type(item) is not dict:return _hold("malformed_cooldown")
  psi=item.get("memory_psi");busy=item.get("gpu_busy")
  if (type(psi) not in (int,float) or not math.isfinite(psi) or
      not 0<=psi<5 or type(busy) is not int or not 0<=busy<=15):
   return _hold("unsafe_cooldown")
 if any(item["memory_psi"]>1 or item["gpu_busy"]>10 for item in cooldown[-3:]):
  return _hold("quiescent_gate_not_met")
 off=receipt.get("runtime_offload")
 if type(off) is not dict:return _hold("missing_loader_report")
 if layers:
  if off.get("summaries")!=[[layers,25],[layers,25]]:
   return _hold("offload_count_mismatch")
  if off.get("assigned_layers")!=list(range(25-layers,25))*2:
   return _hold("offload_layer_assignment_mismatch")
  gpu_buffer=off.get("final_vulkan_model_buffer_mib")
  if type(gpu_buffer) not in (int,float) or not math.isfinite(gpu_buffer) or not 100<gpu_buffer<900:
   return _hold("missing_gpu_model_buffer")
 else:
  if off.get("assigned_layers")!=[] or off.get("summaries") not in ([],[[0,25]],[[0,25],[0,25]]):
   return _hold("cpu_reported_gpu_offload")
 rows=receipt.get("requests")
 if type(rows) is not list or len(rows)!=2:return _hold("incomplete_receipts")
 all_ids=set();pids=set();clients=set();quality_count=0
 for row in rows:
  if type(row) is not dict:return _hold("invalid_receipt_row")
  rid=row.get("id")
  if type(rid) is not str or not 1<=len(rid)<=128 or any(c.isspace() for c in rid):
   return _hold("bad_request_id")
  if rid in all_ids:return _hold("request_replay")
  all_ids.add(rid)
  if row.get("finish_reason") not in ("stop","length"):
   return _hold("request_incomplete")
  usage=row.get("usage");timings=row.get("timings")
  if type(usage) is not dict or type(timings) is not dict:
   return _hold("missing_native_metrics")
  c=usage.get("completion_tokens");p=usage.get("prompt_tokens")
  if (type(c) is not int or not 1<=c<=160 or type(p) is not int or p<=0
      or type(usage.get("total_tokens")) is not int
      or usage["total_tokens"]!=c+p or type(timings.get("predicted_n")) is not int
      or timings["predicted_n"]!=c):
   return _hold("bad_token_accounting")
  for k in ("prompt_ms","predicted_ms","prompt_per_second","predicted_per_second"):
   x=timings.get(k)
   if type(x) not in (int,float) or not math.isfinite(x) or not 0<x<1e8:
    return _hold("invalid_native_timing")
  wall=row.get("wall_ms")
  if type(wall) not in (int,float) or not math.isfinite(wall) or not 0<wall<22000:
   return _hold("invalid_wall_duration")
  # The installed llama.cpp build reports decode tok/s using (n-1)
  # emitted token intervals. Independently cross-check reported speed
  # against the native millisecond field and observed request duration;
  # this rejects forged speed without pretending the counters are signed.
  pred_n=timings["predicted_n"]
  if pred_n<2:
   return _hold("insufficient_native_decode_intervals")
  derived=(pred_n-1)*1000/timings["predicted_ms"]
  claimed=timings["predicted_per_second"]
  if (not math.isfinite(derived) or derived<=0
      or not math.isclose(claimed,derived,rel_tol=0.01,abs_tol=0)
      or timings["predicted_ms"]>wall*1.02):
   return _hold("native_speed_and_duration_inconsistent")
  ident=(row.get("pid"),row.get("process_start_ticks"))
  if any(type(x) is not int or x<=0 for x in ident):
   return _hold("process_identity_missing")
  pids.add(ident)
  for k in ("pre_pressure","post_pressure"):
   x=row.get(k)
   if type(x) not in (int,float) or not math.isfinite(x) or not 0<=x<=1:
    return _hold("unsafe_request_pressure")
  free=row.get("post_vram_free_mib")
  if type(free) not in (int,float) or not math.isfinite(free) or free<1536:
   return _hold("unsafe_gpu_headroom")
  delta=row.get("drm_compute_delta_ns")
  if layers:
   if type(delta) is not int or not 0<delta<10**11:
    return _hold("gpu_engine_activity_missing")
   client=row.get("drm_client")
   if type(client) is not str or not client.isdigit():
    return _hold("gpu_client_invalid")
   clients.add(client)
  elif delta not in (None,0):
   return _hold("cpu_gpu_activity_positive")
  answer=row.get("full_public_synthetic_answer")
  if type(answer) is not str or len(answer)>2000 or hashlib.sha256(answer.encode()).hexdigest()!=row.get("response_sha256"):
   return _hold("answer_digest_mismatch")
  if row.get("response_first_100_chars")!=answer[:100]:
   return _hold("answer_prefix_mismatch")
  grade=exact_task_oracle(task,answer)
  if row.get("semantic_oracle_pass") is not grade:
   return _hold("source_reported_grade_mismatch")
  quality_count+=int(grade)
 if len(pids)!=1 or (layers and len(clients)!=1):
  return _hold("process_or_gpu_client_changed")
 if type(receipt.get("oracle_pass_count")) is not int or receipt["oracle_pass_count"]!=quality_count:
  return _hold("aggregate_oracle_count_inconsistent")
 return {
  "decision":"PASS_SAFE_QUALITY_OBSERVATION_RESEARCH_ONLY",
  "mode":mode,"block":block,"task":task,
  "requests":2,"quality_passes":quality_count,
  "median_native_decode_tps":round(median(r["timings"]["predicted_per_second"] for r in rows),3),
  "median_http_ms":round(median(r["wall_ms"] for r in rows),3),
  "production_authorized":False,
  "externally_attested_gpu_tensor_kernels":False,
 }

def assess_full_matrix(prereg,receipts):
 if type(prereg) is not dict or type(receipts) is not dict:
  return _hold("missing_manifest_or_receipts")
 exp_order=order()
 expected={
  "schema":"lfm3.deathstar.quality-random-preregistered.v1",
  "host":"deathstar-XPS-8920","seed":SEED,
  "source_script_sha256":SOURCE_SHA,
  "model_sha256":PIN,"target_total_requests":24,
  "production_authorized":False,
  "independent_tensor_execution_attested":False,
 }
 for k,want in expected.items():
  if type(prereg.get(k)) is not type(want) or prereg.get(k)!=want:
   return _hold("prereg_"+k)
 if prereg.get("order")!=[
    {"mode":m,"block":b,"task":t} for m,b,t in exp_order]:
  return _hold("preregistered_order_changed")
 oracle_hashes={
    name:hashlib.sha256(canonical(case["expected"]).encode()).hexdigest()
    for name,case in ORACLES.items()}
 if prereg.get("task_oracles_sha256")!=oracle_hashes:
  return _hold("expected_answers_not_preregistered")
 if set(receipts)!=set(exp_order):
  return _hold("missing_or_extra_stage")
 results=[];all_ids=set();all_processes=set()
 for mode,block,task in exp_order:
  source=receipts[(mode,block,task)]
  r=assess_block(source,mode,block,task)
  if r["decision"]!="PASS_SAFE_QUALITY_OBSERVATION_RESEARCH_ONLY":
   return _hold(mode+str(block)+"_"+task+"_"+r["reason"])
  results.append(r)
  ident=(source["requests"][0]["pid"],source["requests"][0]["process_start_ticks"])
  if ident in all_processes:return _hold("process_reused_across_stages")
  all_processes.add(ident)
  for row in source["requests"]:
   if row["id"] in all_ids:return _hold("request_id_repeated_across_blocks")
   all_ids.add(row["id"])
 per_task={}
 per_mode={}
 for task in ORACLES:
  per_task[task]={}
  for mode in MODES:
   chosen=[x for x in results if x["task"]==task and x["mode"]==mode]
   if len(chosen)!=2:return _hold("incomplete_quality_cell")
   per_task[task][mode]={
     "sample_count":4,
     "exact_oracle_passes":sum(x["quality_passes"] for x in chosen),
     "median_block_decode_tps":round(median(x["median_native_decode_tps"] for x in chosen),3),
     "median_block_http_ms":round(median(x["median_http_ms"] for x in chosen),3),
   }
 for mode in MODES:
  per_mode[mode]={
    "sample_count":8,
    "exact_oracle_passes":sum(per_task[t][mode]["exact_oracle_passes"] for t in ORACLES),
  }
 return {
  "decision":"PASS_SAFE_CAPTURE_WITH_EXACT_QUALITY_GATE_RESEARCH_ONLY",
  "blocks_verified":12,"generated_responses_verified":24,
  "quality_pass_count":sum(x["quality_passes"] for x in results),
  "per_task":per_task,"per_mode":per_mode,
  "runtime_latency_is_not_quality_adjusted":True,
  "externally_attested_gpu_tensor_kernels":False,
  "statistical_superiority_established":False,
  "production_authorized":False,
 }
