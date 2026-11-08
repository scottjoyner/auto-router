"""Pure offline long-form counterbalanced Deathstar LFM3 CPU/6/8 audit.

Operates on six operator-provided unsigned receipts (three requests per block).
Never authorizes production, claims independent execution proof, or infers
statistical significance from a six-block single-session experiment.
"""
from __future__ import annotations
import hashlib,json,math,re
from statistics import median

ORDER=(("cpu",1),("vulkan8",1),("vulkan6",1),
       ("vulkan6",2),("vulkan8",2),("cpu",2))
MODEL_PIN="43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452"
PROMPT="List the integers from 1 through 200 in increasing order, separated only by commas and spaces. Do not explain or stop early."
ALIAS="lfm3-deathstar-long-research"
PROMPT_SHA=hashlib.sha256(PROMPT.encode()).hexdigest()
REQUEST_SHA=hashlib.sha256(json.dumps({
    "model":ALIAS,"messages":[{"role":"user","content":PROMPT}],
    "temperature":0,"max_tokens":112,"stream":False,
    "chat_template_kwargs":{"enable_thinking":False}
},sort_keys=True,separators=(",",":")).encode()).hexdigest()

def _hold(reason):
    return {
        "decision":"HOLD","reason":reason,
        "blocks_verified":0,"total_generations":0,
        "observed_fastest_native_decode_mode":None,
        "independent_gpu_tensor_execution_attested":False,
        "cpu_gpu_compute_zero_independently_verified":False,
        "statistical_superiority_established":False,
        "production_authorized":False,
    }

def _safe_capacity(m):
    if not isinstance(m,dict):return False
    for k,threshold in (("MemAvailable_KiB",10*1024*1024),
                        ("SwapFree_KiB",1024*1024)):
        if type(m.get(k)) is not int or m[k]<threshold:return False
    for k,maxval in (("memory_psi_avg10",1.0),("cpu_psi_avg10",2.0),
                     ("io_psi_avg10",5.0)):
        v=m.get(k)
        if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=maxval:
            return False
    if type(m.get("gpu_busy_percent")) is not int or not 0<=m["gpu_busy_percent"]<=10:
        return False
    total=m.get("mem_info_vram_total");used=m.get("mem_info_vram_used")
    return (type(total) is int and type(used) is int and
            0<=used<=total and total-used>=1536*1048576)

def _pos(x):
    return type(x) in (int,float) and math.isfinite(x) and 0<x<1e9

def audit_long_block(d, *,mode,block):
    if mode not in ("cpu","vulkan6","vulkan8") or type(block) is not int or block not in (1,2):
        return _hold("unrecognized_mode")
    if not isinstance(d,dict):return _hold("missing_receipt")
    layers={"cpu":0,"vulkan6":6,"vulkan8":8}[mode]
    expected={
        "schema":"lfm3.deathstar.long-order-v3",
        "host":"deathstar-XPS-8920",
        "mode":mode,"block":block,
        "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
        "model_sha256_expected":MODEL_PIN,
        "model_sha256_observed":MODEL_PIN,
        "device":"none" if mode=="cpu" else "Vulkan0",
        "offloaded_layers_requested":layers,
        "port":"127.0.0.1:19188","alias":ALIAS,
        "requests_requested":3,"requests_attempted":3,
        "max_tokens":112,"prompt_sha256":PROMPT_SHA,
        "request_sha256":REQUEST_SHA,
        "outcome":"PASS_LONG_BLOCK_RESEARCH_ONLY",
        "production_authorized":False,"independent_tensor_execution_attested":False,
        "cpu_mode_gpu_compute_counter_verified":False,
        "credentials_read":False,"existing_services_modified":False,
        "public_ingress_enabled":False,
        "temporary_child_stopped":True,"temporary_log_removed":True,
        "temporary_port_closed":True,
    }
    if any(type(d.get(k)) is not type(v) or d.get(k)!=v for k,v in expected.items()):
        return _hold("identity_or_boundary_mismatch")
    for field in ("preflight","post_hash","capacity_after_cleanup"):
        if not _safe_capacity(d.get(field)):
            return _hold("unsafe_capacity_"+field)
    cooldown=d.get("cooldown")
    if (type(cooldown) is not list or not 3<=len(cooldown)<=18
        or any(type(item) is not dict
               or type(item.get("gpu_busy")) is not int
               or not 0<=item["gpu_busy"]<=15
               or type(item.get("memory_psi")) not in (float,int)
               or not math.isfinite(item["memory_psi"])
               or not 0<=item["memory_psi"]<5
               for item in cooldown)):
        return _hold("unverified_cooldown")
    if any(item["memory_psi"]>1 or item["gpu_busy"]>10 for item in cooldown[-3:]):
        return _hold("post_load_not_stable")
    off=d.get("runtime_offload")
    if type(off) is not dict:return _hold("loader_report_missing")
    sums=off.get("summaries");assigned=off.get("assigned_layers")
    if mode=="cpu":
        if sums not in ([],[[0,25]],[[0,25],[0,25]]) or assigned!=[]:
            return _hold("cpu_unexpected_gpu_layers")
    else:
        if (sums!=[[layers,25],[layers,25]]
                or assigned!=list(range(25-layers,25))*2):
            return _hold("gpu_layer_count_inconsistent")
        buf=off.get("final_vulkan_model_buffer_mib")
        if not _pos(buf) or not 100<=buf<=800:
            return _hold("gpu_model_buffer_missing")
    rows=d.get("requests")
    if type(rows) is not list or len(rows)!=3:return _hold("not_three_complete_responses")
    seen=set();pids=set();gpu_clients=set()
    for row in rows:
        if type(row) is not dict:return _hold("malformed_row")
        uid=row.get("id")
        if type(uid) is not str or not uid or len(uid)>128 or any(x.isspace() for x in uid):
            return _hold("invalid_request_id")
        if uid in seen:return _hold("duplicate_request_id")
        seen.add(uid)
        if row.get("finish_reason") not in ("length","stop"):
            return _hold("invalid_stop")
        prefix=row.get("response_first_100_chars")
        if type(prefix) is not str or not prefix.startswith("1, 2, 3, 4, 5"):
            return _hold("unexpected_text")
        digest=row.get("response_sha256")
        if type(digest) is not str or not re.fullmatch("[0-9a-f]{64}",digest):
            return _hold("invalid_answer_digest")
        if not _pos(row.get("wall_ms")) or row["wall_ms"]>22000:
            return _hold("response_too_slow_or_missing")
        usage=row.get("usage");times=row.get("timings")
        if type(usage) is not dict or type(times) is not dict:
            return _hold("usage_missing")
        if (type(usage.get("completion_tokens")) is not int
                or usage["completion_tokens"]!=112
                or type(usage.get("prompt_tokens")) is not int
                or usage["prompt_tokens"]<=0
                or type(usage.get("total_tokens")) is not int
                or usage["total_tokens"]!=112+usage["prompt_tokens"]
                or type(times.get("predicted_n")) is not int
                or times["predicted_n"]!=112):
            return _hold("token_inconsistency")
        for k in ("predicted_per_second","prompt_per_second","predicted_ms","prompt_ms"):
            if not _pos(times.get(k)):return _hold("bad_native_timing")
        pid=row.get("pid");tick=row.get("process_start_ticks")
        if type(pid) is not int or pid<=0 or type(tick) is not int or tick<=0:
            return _hold("process_identity_missing")
        pids.add((pid,tick))
        for key,maxval in (("pre_pressure",1),("post_pressure",1)):
            n=row.get(key)
            if type(n) not in (float,int) or not math.isfinite(n) or not 0<=n<=maxval:
                return _hold("unsafe_request_memory")
        gpu_free=row.get("post_vram_free_mib")
        if type(gpu_free) not in (int,float) or not math.isfinite(gpu_free) or gpu_free<1536:
            return _hold("unsafe_gpu_memory")
        delta=row.get("drm_compute_delta_ns")
        cid=row.get("drm_client")
        if layers:
            if type(delta) is not int or delta<=0 or delta>10000000000:
                return _hold("gpu_compute_not_seen")
            if type(cid) is not str or not cid.isdigit():
                return _hold("gpu_client_unverified")
            gpu_clients.add(cid)
        else:
            if delta not in (None,0):
                return _hold("cpu_mode_detected_gpu_compute")
    if len(pids)!=1 or (layers and len(gpu_clients)!=1):
        return _hold("session_process_or_gpu_client_changed")
    return {
        "decision":"PASS_LONG_BLOCK_RESEARCH_ONLY",
        "mode":mode,"block":block,"generations":3,
        "median_decode_tps":round(median(x["timings"]["predicted_per_second"] for x in rows),3),
        "median_wall_ms":round(median(x["wall_ms"] for x in rows),3),
        "completion_tokens_per_request":112,
        "gpu_compute_counter_positive_each_request":bool(layers),
        "runtime_reported_layers":layers,
        "independent_gpu_tensor_execution_attested":False,
        "cpu_gpu_compute_zero_independently_verified":False,
        "production_authorized":False,
    }

def audit_counterbalanced_long(receipts):
    if type(receipts) is not dict or set(receipts)!=set(ORDER):
        return _hold("six_expected_blocks_missing")
    blocks=[]
    allids=set()
    for mode,block in ORDER:
        receipt=receipts[(mode,block)]
        result=audit_long_block(receipt,mode=mode,block=block)
        if result["decision"]!="PASS_LONG_BLOCK_RESEARCH_ONLY":
            return _hold("block_"+mode+str(block)+"_"+result["reason"])
        for row in receipt["requests"]:
            if row["id"] in allids:return _hold("repeated_id_across_blocks")
            allids.add(row["id"])
        blocks.append(result)
    summaries={}
    for mode in ("cpu","vulkan6","vulkan8"):
        rows=[row for b in (1,2) for row in receipts[(mode,b)]["requests"]]
        summaries[mode]={
            "requests":6,"generated_tokens":sum(r["usage"]["completion_tokens"] for r in rows),
            "median_native_decode_tps":round(median(r["timings"]["predicted_per_second"] for r in rows),3),
            "median_wall_ms":round(median(r["wall_ms"] for r in rows),3),
            "block_median_decode_tps":[x["median_decode_tps"] for x in blocks if x["mode"]==mode],
        }
    return {
        "decision":"PASS_LONG_COUNTERBALANCED_RESEARCH_ONLY",
        "mode_order":[x[0] for x in ORDER],
        "blocks_verified":6,"total_generations":18,
        "all_outputs_at_max_tokens":True,
        "results":summaries,
        "observed_fastest_native_decode_mode":max(
            summaries,key=lambda x:summaries[x]["median_native_decode_tps"]),
        "independent_gpu_tensor_execution_attested":False,
        "cpu_gpu_compute_zero_independently_verified":False,
        "statistical_superiority_established":False,
        "production_authorized":False,
    }
