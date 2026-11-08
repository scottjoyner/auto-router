"""Pure audit: same-host five-request LFM3 CPU-only vs Vulkan one-layer control.

Runs offline on PUBLIC synthetic or operator-controlled local JSON evidence.
Checks each process's configuration, model, exact usage, AMD GPU counters,
cleanup, and known same prompt source schema. It never produces a production,
kernel attestation, or statistical-confidence release decision.
"""
from __future__ import annotations

import hashlib
import json
import math
from statistics import median

from research.lfm3_top5.gpu_request_audit import (
    EXPECTED_SHA, assess_deathstar_runtime_layer_placement,
)

def _ok_positive(x):
    return type(x) in (int,float) and math.isfinite(x) and x>0

def _canonical_hash(alias):
    payload={
        "model":alias,
        "messages":[{"role":"user","content":"Respond with exactly READY."}],
        "temperature":0,"max_tokens":24,"stream":False,
        "chat_template_kwargs":{"enable_thinking":False},
    }
    return hashlib.sha256(json.dumps(
        payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def _valid_usage(row):
    if type(row) is not dict:
        return False
    if row.get("answer")!="READY" or row.get("finish_reason")!="stop":
        return False
    rid=row.get("id")
    if type(rid) is not str or not 1<=len(rid)<=128 or rid.strip()!=rid or any(c.isspace() for c in rid):
        return False
    if not _ok_positive(row.get("elapsed_ms")) or row["elapsed_ms"]>15000:
        return False
    u=row.get("usage")
    t=row.get("timings")
    if type(u) is not dict or type(t) is not dict:
        return False
    for key in ("prompt_tokens","completion_tokens","total_tokens"):
        if type(u.get(key)) is not int or u[key]<1:
            return False
    if u["completion_tokens"]>24 or u["total_tokens"]!=u["prompt_tokens"]+u["completion_tokens"]:
        return False
    for key in ("prompt_n","predicted_n"):
        if type(t.get(key)) is not int or t[key]<=0:
            return False
    if t["predicted_n"]!=u["completion_tokens"] or t["prompt_n"]>u["prompt_tokens"]:
        return False
    return all(_ok_positive(t.get(key)) for key in
               ("prompt_ms","predicted_ms","prompt_per_second","predicted_per_second"))

def _capacity_ok(c):
    if type(c) is not dict:return False
    limits={"MemAvailable_KiB":10*1024*1024,"SwapFree_KiB":1024*1024}
    if any(type(c.get(k)) is not int or c[k]<v for k,v in limits.items()):
        return False
    for k,maximum in (("memory_psi_avg10",1.0),
                      ("cpu_psi_avg10",2.0),("io_psi_avg10",5.0)):
        x=c.get(k)
        if type(x) not in (int,float) or not math.isfinite(x) or not 0<=x<=maximum:
            return False
    busy=c.get("gpu_busy_percent")
    total=c.get("mem_info_vram_total")
    used=c.get("mem_info_vram_used")
    if (type(busy) is not int or not 0<=busy<=10
        or type(total) is not int or type(used) is not int
        or not 0<=used<=total or total-used<1536*1024*1024):
        return False
    return True

def audit_deathstar_cpu_vulkan_pair(cpu, gpu, *, expected_checkpoint_sha256):
    hold={
        "decision":"HOLD","reason":"inconsistent_or_untrusted_paired_observations",
        "observations_cpu":0,"observations_vulkan":0,
        "runtime_gpu_layer_placement_reported":False,
        "request_correlated_gpu_activity":False,
        "statistical_superiority_established":False,
        "independent_gpu_tensor_execution_attested":False,
        "production_authorized":False,
    }
    if (type(cpu) is not dict or type(gpu) is not dict
        or type(expected_checkpoint_sha256) is not str
        or expected_checkpoint_sha256!=EXPECTED_SHA):
        return hold
    cpu_expected={
        "schema":"lfm3.deathstar.cpu-five.same-qad.v1",
        "host":"deathstar-XPS-8920",
        "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
        "checkpoint_expected_sha256":EXPECTED_SHA,
        "checkpoint_observed_sha256":EXPECTED_SHA,
        "device_selection":"none","gpu_layers_requested":0,
        "host_port":"127.0.0.1:19187",
        "server_alias":"lfm3-deathstar-cpu-research",
        "requested_generations":5,"attempted_generations":5,
        "outcome":"PASS_CPU_CONTROL_RESEARCH_ONLY",
        "credentials_read":False,"existing_services_modified":False,
        "public_ingress_enabled":False,
        "gpu_acceleration_proven":False,"production_authorized":False,
        "temporary_child_stopped":True,"temporary_log_removed":True,
        "temporary_port_closed":True,
    }
    gpu_expected={
        "schema":"lfm3.deathstar.vulkan1-five.same-qad.v1",
        "host":"deathstar-XPS-8920",
        "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
        "checkpoint_expected_sha256":EXPECTED_SHA,
        "checkpoint_observed_sha256":EXPECTED_SHA,
        "gpu_device_requested":"Vulkan0","gpu_layers_requested":1,
        "new_service_binding":"127.0.0.1:19186",
        "requested_generations":5,"attempted_generations":5,
        "outcome":"PASS_VULKAN_ONE_LAYER_FIVE_RESEARCH_ONLY",
        "credentials_read":False,"existing_services_modified":False,
        "new_public_ingress":False,
        "gpu_token_execution_independently_attested":False,
        "production_authorized":False,
        "temporary_child_stopped":True,"temporary_log_removed":True,
        "port_closed_after_cleanup":True,
    }
    for record,expect in ((cpu,cpu_expected),(gpu,gpu_expected)):
        if any(type(record.get(k)) is not type(v) or record.get(k)!=v
               for k,v in expect.items()):
            return hold
    if cpu.get("request_hash")!=_canonical_hash("lfm3-deathstar-cpu-research"):
        return hold
    # CPU implementation explicitly requested none/0 and source inspection
    # confirms the public task shape. The GPU run lacks an independently
    # captured raw request hash, so prompt identity is a source-code claim.
    # Neither is an independent signed request witness.
    if cpu.get("runtime_offload_summary") not in ([],None):
        return hold
    for key in ("capacity_preflight","capacity_after_hash","capacity_after_cleanup"):
        if not _capacity_ok(cpu.get(key)):
            return hold
    for key in ("capacity_preflight","capacity_after_hash",
                "capacity_after_wait","capacity_before_request",
                "capacity_after_cleanup"):
        if not _capacity_ok(gpu.get(key)):
            return hold
    cpu_rows=cpu.get("observations")
    gpu_rows=gpu.get("observations")
    if (type(cpu_rows) is not list or type(gpu_rows) is not list
            or len(cpu_rows)!=5 or len(gpu_rows)!=5):
        return hold
    for rows in (cpu_rows,gpu_rows):
        if any(not _valid_usage(row) for row in rows):
            return hold
        if len({row["id"] for row in rows})!=5:
            return hold
        if any(row["usage"]["completion_tokens"]!=3
               or row["usage"]["prompt_tokens"]!=17 for row in rows):
            return hold

    # Reuse the already negative-tested exact loader+DRM request
    # contract separately for all five GPU responses.
    gpu_deltas=[]
    for row in gpu_rows:
        candidate=dict(gpu)
        candidate["schema"]="lfm3.deathstar.vulkan1-request.final-layer-witness.v5"
        candidate["outcome"]="PASS_GPU_CONTEXT_CORRELATED_RESEARCH_ONLY"
        candidate["attempted_generations"]=1
        candidate["process_gpu_before"]=row.get("gpu_before")
        candidate["process_gpu_after"]=row.get("gpu_after")
        candidate["compute_engine_delta_ns"]=row.get("compute_delta_ns")
        candidate["one_request_elapsed_ms"]=row.get("elapsed_ms")
        candidate["receipt"]={
            "id":row["id"],
            "model_alias":"lfm3-deathstar-vulkan1-research",
            "finish_reason":row["finish_reason"],
            "answer":row["answer"],
            "usage":row["usage"],
            "timings":row["timings"],
        }
        result=assess_deathstar_runtime_layer_placement(
            candidate,expected_checkpoint_sha256=EXPECTED_SHA)
        if result["decision"]!="PASS_RUNTIME_REPORTED_OUTPUT_LAYER_RESEARCH_ONLY":
            return hold
        gpu_deltas.append(result["request_gpu_compute_delta_ns"])
    reported=gpu.get("cumulative_request_compute_delta_ns")
    if (type(reported) is not int or reported!=sum(gpu_deltas)):
        return hold
    def summarize(rows):
        return {
            "median_native_decode_tps":round(median(row["timings"]["predicted_per_second"] for row in rows),3),
            "median_native_prefill_tps":round(median(row["timings"]["prompt_per_second"] for row in rows),3),
            "median_end_to_end_ms":round(median(row["elapsed_ms"] for row in rows),3),
            "decode_tps": [round(row["timings"]["predicted_per_second"],3) for row in rows],
            "http_ms": [row["elapsed_ms"] for row in rows],
        }
    return {
        "decision":"PASS_PAIRED_SHORT_REQUEST_RESEARCH_ONLY",
        "reason":"same_host_pinned_checkpoint_CPU_vs_one_output_layer",
        "observations_cpu":5,"observations_vulkan":5,
        "cpu":summarize(cpu_rows),"vulkan_one_layer":summarize(gpu_rows),
        "all_five_drm_compute_delta_positive":True,
        "total_request_gpu_compute_delta_ns":sum(gpu_deltas),
        "runtime_gpu_layer_placement_reported":True,
        "request_correlated_gpu_activity":True,
        "request_content_independently_signed":False,
        "identical_runtime_binaries_externally_attested":False,
        "statistical_superiority_established":False,
        "independent_gpu_tensor_execution_attested":False,
        "production_authorized":False,
    }
