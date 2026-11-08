"""Pure, offline bounded LFM3 RX480 Vulkan 6/8/10-layer depth-sweep audit.

Inputs are operator-controlled unsigned JSON receipts from one same-host
five-request experiment per depth. The function checks evidence consistency,
not signed GPU kernel identity, benchmark confidence or production authority.
"""
from __future__ import annotations

import math,re
from statistics import median

from research.lfm3_top5.deathstar_pair_audit import (
    EXPECTED_SHA, _capacity_ok, _valid_usage,
    audit_deathstar_cpu_vulkan_pair,
)
from research.lfm3_top5.gpu_request_audit import read_engine_ns

DEPTHS=(6,8,10)

def _hold():
    return {
        "decision":"HOLD",
        "reason":"missing_or_inconsistent_depth_sweep_evidence",
        "runtime_layer_placement_reported":False,
        "per_request_gpu_compute_confirmed":False,
        "statistical_optimum_established":False,
        "independent_tensor_execution_attested":False,
        "production_authorized":False,
    }

def assess_depth_report(data, *, depth, expected_checkpoint_sha256):
    held=_hold()
    if (type(data) is not dict or type(depth) is not int or depth not in DEPTHS
            or expected_checkpoint_sha256!=EXPECTED_SHA):
        return held
    expected={
        "schema":f"lfm3.deathstar.vulkan{depth}-five.same-qad.v1",
        "host":"deathstar-XPS-8920",
        "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
        "checkpoint_expected_sha256":EXPECTED_SHA,
        "checkpoint_observed_sha256":EXPECTED_SHA,
        "gpu_device_requested":"Vulkan0",
        "gpu_layers_requested":depth,
        "new_service_binding":"127.0.0.1:19186",
        "requested_generations":5,"attempted_generations":5,
        "outcome":{
            6:"PASS_VULKAN_SIX_LAYERS_FIVE_RESEARCH_ONLY",
            8:"PASS_VULKAN_EIGHT_LAYERS_FIVE_RESEARCH_ONLY",
            10:"PASS_VULKAN_TEN_LAYERS_FIVE_RESEARCH_ONLY",
        }[depth],
        "credentials_read":False,"existing_services_modified":False,
        "new_public_ingress":False,
        "gpu_token_execution_independently_attested":False,
        "production_authorized":False,
        "temporary_child_stopped":True,"temporary_log_removed":True,
        "port_closed_after_cleanup":True,
        "runtime_reported_output_layer_gpu":True,
    }
    if any(type(data.get(k)) is not type(v) or data.get(k)!=v
           for k,v in expected.items()):
        return held
    for key in ("capacity_preflight","capacity_after_hash",
                "capacity_after_wait","capacity_before_request",
                "capacity_after_cleanup"):
        if not _capacity_ok(data.get(key)):
            return held
    counts=data.get("server_reported_offload_counts")
    assigned=data.get("runtime_reported_assigned_layers")
    lines=data.get("startup_offload_lines")
    if (type(counts) is not list or len(counts)!=2
            or any(type(x) is not dict or x.get("layers")!=depth
                   or x.get("layers_total")!=25 for x in counts)
            or type(assigned) is not list or len(assigned)!=depth*2
            or assigned!=list(range(25-depth,25))*2
            or type(lines) is not list or not depth*2+8<=len(lines)<=100
            or any(type(x) is not str or len(x)>230 or len(x)<1
                   or "\n" in x or "\r" in x or any(s in x.lower() for s in
                    ("api-key","authorization","bearer ","password=","token="))
                   for x in lines)):
        return held
    summaries=[]
    assigned_in_lines=[]
    model_buffers=[]
    compute_buffers=[]
    devices=0
    output_mentions=0
    for line in lines:
        m=re.search(r"load_tensors:\s+offloaded\s+(\d+)\s*/\s*(\d+)\s+layers\s+to\s+GPU",
                    line,re.IGNORECASE)
        if m:summaries.append((int(m.group(1)),int(m.group(2))))
        m=re.search(r"load_tensors:\s+layer\s+(\d+)\s+assigned\s+to\s+device\s+Vulkan0\b",
                    line,re.IGNORECASE)
        if m:assigned_in_lines.append(int(m.group(1)))
        m=re.search(r"Vulkan0\s+model\s+buffer\s+size\s*=\s*([0-9]+(?:\.[0-9]+)?)\s+MiB",
                    line,re.IGNORECASE)
        if m:model_buffers.append(float(m.group(1)))
        m=re.search(r"Vulkan0\s+compute\s+buffer\s+size\s*=\s*([0-9]+(?:\.[0-9]+)?)\s+MiB",
                    line,re.IGNORECASE)
        if m:compute_buffers.append(float(m.group(1)))
        if ("using device Vulkan0" in line and
                "AMD Radeon RX 480" in line and "0000:01:00.0" in line):
            devices+=1
        if re.search("offloading output layer to GPU",line,re.IGNORECASE):
            output_mentions+=1
    if (summaries!=[(depth,25)]*2 or
            assigned_in_lines!=list(range(25-depth,25))*2 or
            devices<1 or output_mentions<1 or not model_buffers or
            not compute_buffers or not all(math.isfinite(x) and x>=0
                   for x in model_buffers+compute_buffers)):
        return held
    buffer=data.get("runtime_reported_vulkan_model_buffer_mib")
    if (type(buffer) not in (int,float) or not math.isfinite(buffer)
            or buffer!=model_buffers[-1]
            or not 200<=buffer<=1000
            or not 20<=compute_buffers[-1]<=500):
        return held
    rows=data.get("observations")
    if type(rows) is not list or len(rows)!=5 or any(not _valid_usage(row) for row in rows):
        return held
    if len({row["id"] for row in rows})!=5:return held
    if any(row["usage"]["prompt_tokens"]!=17
           or row["usage"]["completion_tokens"]!=3 for row in rows):
        return held
    all_pids=set()
    all_clients=set()
    deltas=[]
    for row in rows:
        before=row.get("gpu_before")
        after=row.get("gpu_after")
        if type(before) is not dict or type(after) is not dict:return held
        for key in ("pid","startticks"):
            if type(before.get(key)) is not int or before[key]<=0 or before[key]!=after.get(key):
                return held
        b=read_engine_ns(before)
        a=read_engine_ns(after)
        if b is None or a is None or a<=b:return held
        cid_before=before["amd_clients"][0]["drm-client-id"]
        cid_after=after["amd_clients"][0]["drm-client-id"]
        if cid_before!=cid_after:return held
        delta=row.get("compute_delta_ns")
        if type(delta) is not int or delta!=a-b or delta>10**11:
            return held
        deltas.append(delta)
        all_pids.add((before["pid"],before["startticks"]))
        all_clients.add(cid_before)
    if len(all_pids)!=1 or len(all_clients)!=1:
        return held
    summed=data.get("cumulative_request_compute_delta_ns")
    if type(summed) is not int or summed!=sum(deltas):
        return held
    return {
        "decision":"PASS_DEPTH_RESEARCH_ONLY",
        "offloaded_layers":depth,
        "runtime_layer_placement_reported":True,
        "gpu_model_buffer_mib":buffer,
        "per_request_gpu_compute_confirmed":True,
        "gpu_compute_delta_ns_per_request":deltas,
        "median_native_decode_tps":round(median(row["timings"]["predicted_per_second"] for row in rows),3),
        "median_end_to_end_ms":round(median(row["elapsed_ms"] for row in rows),3),
        "observations":5,
        "statistical_optimum_established":False,
        "independent_tensor_execution_attested":False,
        "production_authorized":False,
    }

def assess_full_deathstar_sweep(cpu,gpu_one,depth_reports,*,expected_checkpoint_sha256):
    held=_hold()
    if type(depth_reports) is not dict or set(depth_reports)!=set(DEPTHS):
        return held
    base=audit_deathstar_cpu_vulkan_pair(
        cpu,gpu_one,expected_checkpoint_sha256=expected_checkpoint_sha256)
    if base["decision"]!="PASS_PAIRED_SHORT_REQUEST_RESEARCH_ONLY":
        return held
    checks={}
    for depth in DEPTHS:
        result=assess_depth_report(
            depth_reports[depth],depth=depth,
            expected_checkpoint_sha256=expected_checkpoint_sha256)
        if result["decision"]!="PASS_DEPTH_RESEARCH_ONLY":
            return held
        checks[str(depth)]=result
    return {
        "decision":"PASS_EXPLORATORY_FIVE_MODE_RESEARCH_ONLY",
        "checkpoint_sha256":EXPECTED_SHA,
        "cpu_control":base["cpu"],
        "vulkan_one_layer":base["vulkan_one_layer"],
        "vulkan_depths":checks,
        "five_requests_each_mode":True,
        "runtime_layer_placement_reported":True,
        "per_request_gpu_compute_confirmed":True,
        "observed_vulkan_depth_with_max_median_decode_tps":max(
            DEPTHS,key=lambda d:checks[str(d)]["median_native_decode_tps"]),
        "statistical_optimum_established":False,
        "independent_tensor_execution_attested":False,
        "production_authorized":False,
    }
