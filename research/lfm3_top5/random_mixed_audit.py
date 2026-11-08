"""Pure offline audit for pre-registered Deathstar randomized mixed workload.

Checks 12 fixed-seed blocks, 24 completions, two independent process starts
per (mode,input/output length), pinned local checkpoint and exact known
request hashes, safe capacity and cleanup. Receipts are unsigned local
operator records. Positive GPU DRM deltas are source-reported: the JSON
does not include raw per-request fdinfo before/after, so this assessor
does NOT independently authenticate GPU kernel dispatch or tensor identity.

ALWAYS fail closed on evidence gaps and deny statistical significance
and production authorization, including a passing research bundle.
"""
from __future__ import annotations
import hashlib, math, random, re
from statistics import median

MODEL_SHA="43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452"
SOURCE_SHA="aadf4e9c8ac3dde2a36a676fa141d122477973380444d549deb75745e1e8f851"
SEED=202611081321
GROUPS=(("short",1),("long",1),("long",2),("short",2))
MODES=("cpu","vulkan6","vulkan8")
WORKLOADS={
    "short":{
        "prompt_len":124,"max_tokens":64,
        "prompt_sha256":"99dc298a87e3fd6ca3f5e0f5742cafcf5e0311451c1ffcb4180a6f1b7b2921f7",
        "request_sha256":"221e8acbd8e61867bc89396f724f50ac037075105abd97e7ffaf2ab2282c9ddc",
    },
    "long":{
        "prompt_len":1456,"max_tokens":160,
        "prompt_sha256":"7d6d7f83941441ecfebbff69878236d68bb9dd3d564b4876b4dfd9c2c253c151",
        "request_sha256":"f40f7bcd8c2064a3cb411de6418bd2c000046f44756a5feb68f6bc25aadc4cfe",
    },
}
def plan():
    rng=random.Random(SEED)
    return [(mode,block,workload) for workload,block in GROUPS
            for mode in rng.sample(MODES,k=len(MODES))]

def held(reason):
    return {
        "decision":"HOLD","reason":reason,
        "blocks_verified":0,"generations_verified":0,
        "significant_superiority_established":False,
        "independent_kernel_execution_attested":False,
        "cpu_zero_gpu_activity_verified":False,
        "production_authorized":False,
    }

def safe(m):
    if type(m) is not dict:return False
    for key,minval in (("MemAvailable_KiB",10*1024*1024),
                       ("SwapFree_KiB",1024*1024)):
        if type(m.get(key)) is not int or m[key]<minval:return False
    for key,maxval in (("memory_psi_avg10",1),("cpu_psi_avg10",2),("io_psi_avg10",5)):
        value=m.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=maxval:
            return False
    busy=m.get("gpu_busy_percent")
    total=m.get("mem_info_vram_total")
    used=m.get("mem_info_vram_used")
    return (type(busy) is int and 0<=busy<=10 and
            type(total) is int and type(used) is int and
            total>=0 and 0<=used<=total and total-used>=1536*1024*1024)

def audit_block(d,mode,block,workload):
    if (mode not in MODES or block not in (1,2) or workload not in WORKLOADS):
        return held("invalid_mode_or_block")
    if type(d) is not dict:return held("missing_report")
    layers={"cpu":0,"vulkan6":6,"vulkan8":8}[mode]
    config=WORKLOADS[workload]
    must={
        "schema":"lfm3.deathstar.mixed-random-block-v2",
        "host":"deathstar-XPS-8920",
        "mode":mode,"block":block,"workload":workload,
        "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
        "model_sha256_expected":MODEL_SHA,
        "model_sha256_observed":MODEL_SHA,
        "device":"none" if layers==0 else "Vulkan0",
        "offloaded_layers_requested":layers,
        "port":"127.0.0.1:19189",
        "alias":"lfm3-deathstar-mixed-research",
        "requests_requested":2,"requests_attempted":2,
        "max_tokens":config["max_tokens"],
        "prompt_sha256":config["prompt_sha256"],
        "request_sha256":config["request_sha256"],
        "outcome":"PASS_LONG_BLOCK_RESEARCH_ONLY",
        "production_authorized":False,
        "independent_tensor_execution_attested":False,
        "cpu_mode_gpu_compute_counter_verified":False,
        "credentials_read":False,"existing_services_modified":False,
        "public_ingress_enabled":False,
        "temporary_child_stopped":True,
        "temporary_log_removed":True,
        "temporary_port_closed":True,
    }
    for k,value in must.items():
        if type(d.get(k)) is not type(value) or d[k]!=value:
            return held("identity_or_boundary_"+k)
    for key in ("preflight","post_hash","capacity_after_cleanup"):
        if not safe(d.get(key)):
            return held("unsafe_"+key)
    cool=d.get("cooldown")
    if type(cool) is not list or not 3<=len(cool)<=18:
        return held("no_quiescent_cooldown")
    for item in cool:
        if type(item) is not dict or type(item.get("gpu_busy")) is not int or not 0<=item["gpu_busy"]<=15:
            return held("unsafe_cooldown_gpu")
        psi=item.get("memory_psi")
        if type(psi) not in (int,float) or not math.isfinite(psi) or not 0<=psi<5:
            return held("unsafe_cooldown_memory")
    if any(x["gpu_busy"]>10 or x["memory_psi"]>1 for x in cool[-3:]):
        return held("final_cooldown_not_quiescent")
    off=d.get("runtime_offload")
    if type(off) is not dict:return held("no_offload_log")
    summaries=off.get("summaries")
    assigned=off.get("assigned_layers")
    if layers:
        if summaries!=[[layers,25]]*2:
            return held("offload_summary_mismatch")
        if assigned!=list(range(25-layers,25))*2:
            return held("loader_assigned_layers_mismatch")
        vram=off.get("final_vulkan_model_buffer_mib")
        if type(vram) not in (float,int) or not math.isfinite(vram) or not 100<vram<900:
            return held("model_buffer_unavailable")
    else:
        if summaries not in ([],[[0,25]],[[0,25],[0,25]]) or assigned!=[]:
            return held("cpu_offload_detected")
    rows=d.get("requests")
    if type(rows) is not list or len(rows)!=2:return held("not_two_responses")
    unique=set();process=set();client=set()
    for row in rows:
        if type(row) is not dict:return held("row_untrusted")
        rid=row.get("id")
        if type(rid) is not str or not 1<=len(rid)<=128 or any(ch.isspace() for ch in rid):
            return held("invalid_response_id")
        if rid in unique:return held("duplicate_response_id")
        unique.add(rid)
        if row.get("finish_reason") not in ("length","stop"):
            return held("missing_response_termination")
        text=row.get("response_first_100_chars")
        if type(text) is not str or not text.startswith("1, 2, 3, 4, 5, 6, 7, 8, 9, 10") or "<think>" in text or "</think>" in text:
            return held("output_quality_not_list")
        digest=row.get("response_sha256")
        if type(digest) is not str or re.fullmatch("[0-9a-f]{64}",digest) is None:
            return held("output_digest_missing")
        usage=row.get("usage");timing=row.get("timings")
        if type(usage) is not dict or type(timing) is not dict:return held("usage_not_present")
        if (type(usage.get("completion_tokens")) is not int or
            usage["completion_tokens"]!=config["max_tokens"] or
            type(usage.get("prompt_tokens")) is not int or
            not 8<=usage["prompt_tokens"]<=1800 or
            type(usage.get("total_tokens")) is not int or
            usage["total_tokens"]!=usage["prompt_tokens"]+usage["completion_tokens"] or
            type(timing.get("predicted_n")) is not int or
            timing["predicted_n"]!=usage["completion_tokens"]):
            return held("token_count_mismatch")
        for k in ("prompt_ms","predicted_ms","prompt_per_second","predicted_per_second"):
            val=timing.get(k)
            if type(val) not in (int,float) or not math.isfinite(val) or not 0<val<10**8:
                return held("invalid_native_timing")
        wall=row.get("wall_ms")
        if type(wall) not in (float,int) or not math.isfinite(wall) or not 0<wall<22000:
            return held("invalid_end_to_end")
        ident=(row.get("pid"),row.get("process_start_ticks"))
        if any(type(n) is not int or n<=0 for n in ident):
            return held("invalid_linux_process_identity")
        process.add(ident)
        for pressure in ("pre_pressure","post_pressure"):
            val=row.get(pressure)
            if type(val) not in (float,int) or not math.isfinite(val) or not 0<=val<=1:
                return held("unsafe_request_memory")
        free=row.get("post_vram_free_mib")
        if type(free) not in (int,float) or not math.isfinite(free) or free<1536:
            return held("unsafe_request_vram")
        delta=row.get("drm_compute_delta_ns")
        if layers:
            if type(delta) is not int or not 0<delta<10**11:
                return held("gpu_engine_counter_missing")
            cid=row.get("drm_client")
            if type(cid) is not str or not cid.isdigit():return held("invalid_gpu_client_id")
            client.add(cid)
        elif delta not in (None,0):
            return held("positive_cpu_gpu_counter")
    if len(process)!=1 or (layers and len(client)!=1):
        return held("per_block_process_or_gpu_client_changed")
    return {
        "decision":"PASS_MIXED_BLOCK_RESEARCH_ONLY",
        "mode":mode,"block":block,"workload":workload,
        "generations":2,"reported_gpu_layers":layers,
        "median_native_decode_tps":round(median(x["timings"]["predicted_per_second"] for x in rows),3),
        "median_wall_ms":round(median(x["wall_ms"] for x in rows),3),
        "cpu_gpu_activity_zero_verified":False,
        "independent_kernel_execution_attested":False,
        "production_authorized":False,
    }

def audit_random_mixed(manifest,receipts):
    if type(manifest) is not dict or type(receipts) is not dict:
        return held("manifest_or_receipts_missing")
    expected_plan=plan()
    target=[
        {"mode":m,"block":b,"workload":w}
        for m,b,w in expected_plan
    ]
    required={
        "schema":"lfm3.deathstar.mixed-random-prereg.v1",
        "host":"deathstar-XPS-8920",
        "preregistered_before_runs":True,
        "random_seed":SEED,
        "checkpoint_sha256":MODEL_SHA,
        "script_sha256":SOURCE_SHA,
        "target_requests":24,
        "production_authorized":False,
        "independent_gpu_tensor_execution_attested":False,
        "statistical_superiority_established":False,
    }
    for k,value in required.items():
        if type(manifest.get(k)) is not type(value) or manifest[k]!=value:
            return held("prereg_mismatch_"+k)
    if manifest.get("order")!=target or set(receipts)!=set(expected_plan):
        return held("reordered_or_missing_preregistered_blocks")
    verified=[];unique_ids=set();process_ids=set()
    for mode,block,workload in expected_plan:
        d=receipts[(mode,block,workload)]
        b=audit_block(d,mode,block,workload)
        if b["decision"]!="PASS_MIXED_BLOCK_RESEARCH_ONLY":
            return held("block_"+mode+str(block)+workload+"_"+b["reason"])
        verified.append(b)
        ident=(d["requests"][0]["pid"],d["requests"][0]["process_start_ticks"])
        if ident in process_ids:return held("process_reused_across_stages")
        process_ids.add(ident)
        for row in d["requests"]:
            if row["id"] in unique_ids:return held("response_id_repeated_across_stages")
            unique_ids.add(row["id"])
    summaries={}
    for workload in ("short","long"):
        summaries[workload]={}
        for mode in MODES:
            items=[row for block in (1,2)
                for row in receipts[(mode,block,workload)]["requests"]]
            if len(items)!=4:return held("incomplete_mode_workload_cell")
            prompts={x["usage"]["prompt_tokens"] for x in items}
            if len(prompts)!=1:return held("prompt_accounting_drift")
            summaries[workload][mode]={
                "requests":4,"completion_tokens_per_request":WORKLOADS[workload]["max_tokens"],
                "observed_prompt_tokens_per_request":next(iter(prompts)),
                "median_native_decode_tps":round(median(x["timings"]["predicted_per_second"] for x in items),3),
                "median_wall_ms":round(median(x["wall_ms"] for x in items),3),
            }
    return {
        "decision":"PASS_RANDOM_MIXED_RESEARCH_ONLY",
        "seed":SEED,"preregistered_order":target,
        "blocks_verified":12,"generations_verified":24,
        "all_inference_requests_content_hash_matched":True,
        "sampled_output_length_tokens":{"short":64,"long":160},
        "per_workload":summaries,
        "best_observed_native_decode_mode_by_workload":{
            w:max(MODES,key=lambda m:summaries[w][m]["median_native_decode_tps"])
            for w in ("short","long")},
        "significant_superiority_established":False,
        "independent_kernel_execution_attested":False,
        "cpu_zero_gpu_activity_verified":False,
        "production_authorized":False,
    }
