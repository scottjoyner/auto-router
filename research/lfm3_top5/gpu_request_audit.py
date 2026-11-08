"""Pure offline auditor of a single Deathstar Vulkan LFM3 research receipt.

No filesystem, process, device, network or credential access. Operator-produced
JSON is NOT externally signed custody; this cannot authorize production or
independently certify GPU tensor placement even on valid correlated counters.
"""
from __future__ import annotations
import math
import re

EXPECTED_MODEL="LFM3-1.7B-QAD-Q4_0.gguf"
EXPECTED_HOST="deathstar-XPS-8920"
EXPECTED_SHA="43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452"
ALIASES=("lfm3-deathstar-vulkan1-research",)

def positive_finite(x):
    return type(x) in (int,float) and math.isfinite(x) and x>0

def read_engine_ns(record):
    if type(record) is not dict:return None
    clients=record.get("amd_clients")
    if type(clients) is not list or len(clients)!=1:return None
    item=clients[0]
    if type(item) is not dict:return None
    for k,v in (("drm-driver","amdgpu"),("drm-pdev","0000:01:00.0"),
                ("device","/dev/dri/renderD128")):
        if item.get(k)!=v:return None
    if type(item.get("drm-client-id")) is not str or not item["drm-client-id"].isdigit():
        return None
    value=item.get("drm-engine-compute")
    if type(value) is not str or not re.fullmatch(r"[0-9]{1,20} ns",value):
        return None
    number=int(value[:-3])
    if number>=2**63:return None
    return number

def assess_deathstar_gpu_request(bundle, *, expected_checkpoint_sha256):
    """Return PASS_CORRELATED_GPU_PROCESS_RESEARCH_ONLY or HOLD, never prod."""
    held={
        "decision":"HOLD","correlated_process_gpu_activity":False,
        "actual_gpu_tensor_layer_placement_verified":False,
        "independent_gpu_execution_attested":False,
        "production_authorized":False,
        "compute_engine_delta_ns":0,
        "reason":"missing_or_untrusted_request_observation",
    }
    if type(bundle) is not dict:return held
    if type(expected_checkpoint_sha256) is not str or expected_checkpoint_sha256!=EXPECTED_SHA:
        return held
    expected={
        "schema":"lfm3.deathstar.vulkan1-request.v1",
        "host":EXPECTED_HOST,
        "outcome":"PASS_GPU_CONTEXT_CORRELATED_RESEARCH_ONLY",
        "checkpoint_expected_sha256":EXPECTED_SHA,
        "checkpoint_observed_sha256":EXPECTED_SHA,
        "model_basename":EXPECTED_MODEL,
        "gpu_device_requested":"Vulkan0",
        "new_service_binding":"127.0.0.1:19186",
        "attempted_generations":1,
        "credentials_read":False,
        "existing_services_modified":False,
        "new_public_ingress":False,
        "gpu_token_execution_independently_attested":False,
        "production_authorized":False,
        "temporary_child_stopped":True,
        "temporary_log_removed":True,
        "port_closed_after_cleanup":True,
    }
    if any(type(bundle.get(k)) is not type(v) or bundle.get(k)!=v for k,v in expected.items()):
        return held
    if type(bundle.get("gpu_layers_requested")) is not int or not 1<=bundle["gpu_layers_requested"]<=4:
        return held
    if type(bundle.get("compute_engine_delta_ns")) is not int:
        return held
    if not 0<bundle["compute_engine_delta_ns"]<10**11:
        return held
    first=bundle.get("process_gpu_before")
    last=bundle.get("process_gpu_after")
    if type(first) is not dict or type(last) is not dict:return held
    for k in ("pid","startticks"):
        if type(first.get(k)) is not int or first[k]<=0 or first.get(k)!=last.get(k):
            return held
    a=read_engine_ns(first)
    b=read_engine_ns(last)
    if a is None or b is None or b<=a or b-a !=bundle["compute_engine_delta_ns"]:
        return held
    ca=first["amd_clients"][0]
    cb=last["amd_clients"][0]
    if ca["drm-client-id"]!=cb["drm-client-id"]:return held
    for name in ("capacity_preflight","capacity_after_hash",
                 "capacity_after_wait","capacity_before_request"):
        x=bundle.get(name)
        if type(x) is not dict:return held
        for key,lower in (("MemAvailable_KiB",10*1024*1024),
                          ("SwapFree_KiB",1024*1024)):
            if type(x.get(key)) is not int or x[key]<lower:return held
        for key,upper in (("memory_psi_avg10",1.0),
                          ("cpu_psi_avg10",2.0),
                          ("io_psi_avg10",5.0)):
            val=x.get(key)
            if type(val) not in (int,float) or not math.isfinite(val) or val<0 or val>upper:
                return held
        if type(x.get("gpu_busy_percent")) is not int or not 0<=x["gpu_busy_percent"]<=10:
            return held
        total=x.get("mem_info_vram_total")
        used=x.get("mem_info_vram_used")
        if (type(total) is not int or type(used) is not int or
                used<0 or used>total or total-used<1536*1024*1024):
            return held
    duration=bundle.get("one_request_elapsed_ms")
    if not positive_finite(duration) or duration>15000:return held
    receipt=bundle.get("receipt")
    if type(receipt) is not dict:return held
    uid=receipt.get("id")
    if type(uid) is not str or not 1<=len(uid)<=128 or uid.strip()!=uid or any(c.isspace() for c in uid):
        return held
    if (receipt.get("model_alias") not in ALIASES
            or receipt.get("finish_reason")!="stop"
            or type(receipt.get("answer")) is not str
            or receipt["answer"].strip()!="READY"):
        return held
    usage=receipt.get("usage")
    timings=receipt.get("timings")
    if type(usage) is not dict or type(timings) is not dict:return held
    for k in ("prompt_tokens","completion_tokens","total_tokens"):
        if type(usage.get(k)) is not int or usage[k]<=0:return held
    if (usage["prompt_tokens"]+usage["completion_tokens"]!=usage["total_tokens"]
            or usage["completion_tokens"]>24):
        return held
    for k in ("prompt_n","predicted_n"):
        if type(timings.get(k)) is not int or timings[k]<=0:return held
    if (usage["completion_tokens"]!=timings["predicted_n"]
            or usage["prompt_tokens"]<timings["prompt_n"]):
        return held
    for k in ("prompt_ms","predicted_ms","prompt_per_second","predicted_per_second"):
        if not positive_finite(timings.get(k)):return held
    return {
        "decision":"PASS_CORRELATED_GPU_PROCESS_RESEARCH_ONLY",
        "reason":"single_bounded_request_with_same_process_amd_compute_delta",
        "correlated_process_gpu_activity":True,
        "compute_engine_delta_ns":bundle["compute_engine_delta_ns"],
        "native_prefill_tps":round(timings["prompt_per_second"],3),
        "native_decode_tps":round(timings["predicted_per_second"],3),
        "response_elapsed_ms":round(duration,3),
        "actual_gpu_tensor_layer_placement_verified":False,
        "independent_gpu_execution_attested":False,
        "production_authorized":False,
    }
