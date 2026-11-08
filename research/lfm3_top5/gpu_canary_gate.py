"""Pure, synthetic-readiness gate for LFM3 GPU research. Never authorizes runs.

The metrics in untrusted local snapshots are advisory only. Real execution
requires separate physical capacity checks, operator approval and independent
device-dispatch evidence. No subprocess/network/filesystem access in module.
"""
from __future__ import annotations

import math

HOSTS=("x1-370","xwing","joyner")

def literal_nonnegative_int(v):
    return type(v) is int and 0<=v<2**63

def assess_gpu_canary_capacity(sample: object, expected_host: str) -> dict:
    """Capacity triage only. ALWAYS HOLD for real GPU inference/deploy.

    Numeric self-reported telemetry is not a physically signed witness or
    an operator-approved execution grant. Never returns an admit decision.
    """
    base={
        "decision":"HOLD","capacity_candidate":False,
        "gpu_token_execution_verified":False,
        "production_authorized":False,
        "operator_authorization_verified":False,
        "reasons":[],
    }
    reasons=[]
    if expected_host not in HOSTS or type(sample) is not dict:
        base["reasons"]=["invalid_host_or_snapshot"];return base
    if sample.get("hostname") != expected_host:
        reasons.append("host_identity_mismatch")
    for k,v in (("read_only",True),("credentials_read",False),
                ("host_config_changes",False),("model_loads",0),
                ("generation_requests",0)):
        if type(sample.get(k)) is not type(v) or sample.get(k)!=v:
            reasons.append("collection_boundary_unproven_"+k)
    memory=sample.get("memory_kib")
    if type(memory) is not dict:memory={}
    mem=memory.get("MemAvailable")
    swap=memory.get("SwapFree")
    psi=sample.get("memory_psi_avg10")
    if not literal_nonnegative_int(mem) or mem<(
            8*1024*1024 if expected_host=="x1-370" else
            5*1024*1024 if expected_host=="xwing" else
            2*1024*1024):
        reasons.append("low_or_unknown_ram_headroom")
    if not literal_nonnegative_int(swap) or swap<1024*1024:
        reasons.append("low_or_unknown_swap_headroom")
    if type(psi) not in (float,int) or not math.isfinite(psi) or psi>0.1 or psi<0:
        reasons.append("memory_pressure_unverified")
    count=sample.get("server_count")
    if type(count) is not int or count<0 or count>50:
        reasons.append("server_inventory_unverified")
    # One controlled GPU canary must not automatically compete with existing
    # apps. 100% gfx is a blocker; GPU idle requires at least 2 samples.
    stats=sample.get("gpu")
    if (type(stats) is not list or not 1<=len(stats)<=8
            or any(type(entry) is not dict for entry in stats)):
        reasons.append("kernel_gpu_telemetry_unverified")
        gpu={}
    elif expected_host=="joyner":
        # Joyner has both iGPU and a discrete R9700. The dGPU does not
        # expose gpu_busy_percent on this kernel; unknown remains HOLD.
        candidates=[entry for entry in stats
                    if literal_nonnegative_int(entry.get("mem_info_vram_total"))
                    and entry["mem_info_vram_total"]>=16*1024**3]
        if len(candidates)!=1:
            reasons.append("kernel_gpu_telemetry_unverified")
            gpu={}
        else:gpu=candidates[0]
    elif len(stats)!=1:
        reasons.append("kernel_gpu_telemetry_unverified")
        gpu={}
    else:gpu=stats[0]
    busy=gpu.get("gpu_busy_percent")
    if not literal_nonnegative_int(busy) or busy>35:
        reasons.append("gpu_contention_or_busy_unknown")
    amd=sample.get("amd_smi")
    if type(amd) is not list or len(amd)!=1 or type(amd[0]) is not dict:
        reasons.append("amd_smi_gpu_telemetry_missing")
        item={}
    else:
        item=amd[0]
        if type(item.get("gpu")) is not int:
            reasons.append("amd_smi_gpu_identity_missing")
        gfx=item.get("gfx")
        if type(gfx) is dict:gfx=gfx.get("value")
        if not literal_nonnegative_int(gfx) or gfx>35:
            reasons.append("amd_smi_busy_or_unavailable")
    total=gpu.get("mem_info_vram_total")
    used=gpu.get("mem_info_vram_used")
    if expected_host=="xwing":
        # gfx1151 Strix Halo uses unified shared memory: the kernel's
        # 512MiB "VRAM" aperture differs from amd-smi's 24GiB-addressable
        # GPU pool. Do not declare starvation from the aperture alone.
        free=item.get("vram_free")
        free_mb=free.get("value") if type(free) is dict else None
        if not literal_nonnegative_int(free_mb) or free_mb<2048:
            reasons.append("gpu_vram_headroom_insufficient")
    elif (not literal_nonnegative_int(total)
          or not literal_nonnegative_int(used)
          or used>total or total-used<512*1024*1024):
        reasons.append("gpu_vram_headroom_insufficient")
    # These are authorization/provenance boundaries, not metrics that can be
    # promoted by assigning booleans inside an untrusted JSON snapshot.
    if expected_host=="joyner":
        reasons.append("protected_joyner_existing_service_requires_approved_client")
    if expected_host=="xwing" and type(count) is int and count>=3:
        reasons.append("existing_xwing_model_workload_contention")
    reasons.extend((
        "independent_gpu_dispatch_witness_missing",
        "external_operator_authorization_missing",
    ))
    base["capacity_candidate"]=not any(r for r in reasons if r not in (
        "independent_gpu_dispatch_witness_missing",
        "external_operator_authorization_missing"))
    base["reasons"]=sorted(set(reasons))
    return base
