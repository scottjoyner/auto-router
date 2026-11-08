"""Pure read-only Joyner R9700 GPU admission evaluator.

Separates supported physical RDNA4 GPU/driver, existing Vulkan process
mapping, and safe admission for NEW model loads or protected generations.
Never recommends touching keys, reconfiguring Tailscale, stopping a
production model, increasing swap, installing packages or changing routes.
All source observations are local operator-controlled and unsigned.
"""
from __future__ import annotations
import math,re

PINNED_BASENAME="LFM3-1.7B-QAD-Q4_0.gguf"
PINNED_SIZE=1049615680
R9700_PCI_ID="0x7551"
REQUIRED_PDEV="0000:03:00.0"
REQUIRED_GPU_BYTES=32*1024*1024*1024
NEW_MODEL_MIN_RAM_KIB=10*1024*1024
NEW_MODEL_MIN_SWAP_KIB=1024*1024
NEW_MODEL_MIN_VRAM_BYTES=1536*1024*1024
NEW_MODEL_MIN_GTT_BYTES=2*1024*1024*1024

def _positive_int(v):
    return type(v) is int and v>=0

def _denied(reason):
    return {
        "decision":"HOLD",
        "hardware_and_gpu_context_verified":False,
        "existing_protected_service_healthy":False,
        "new_model_canary_admitted":False,
        "protected_generation_authorized":False,
        "production_authorized":False,
        "independent_gpu_token_execution_attested":False,
        "live_gpu_saturation_measured":False,
        "reason_codes":[reason],
    }

def audit_joyner_readonly(data):
    """Fail closed on any source/host mismatch or untrusted type.

    Even a hypothetical resource-sufficient, successfully mapped card
    cannot authorize any model POST or production route. That requires
    separately verified operator rights, provenance and capability gate.
    """
    if type(data) is not dict or data.get("schema")!="lfm3.joyner.gpu-capacity-readonly.v1":
        return _denied("missing_or_untrusted_report")
    strict={
        "host":"joyner",
        "uid":1001,
        "read_only":True,
        "model_requests_sent":0,
        "credentials_accessed":False,
        "configuration_changed":False,
        "public_ingress_modified":False,
        "new_models_loaded":False,
        "canonical_ssh_hostkey_enforced":True,
        "source_independently_attested":False,
    }
    for key,want in strict.items():
        if type(data.get(key)) is not type(want) or data[key]!=want:
            return _denied("source_identity_or_safety_flag_"+key)
    gpu=data.get("discrete_gpu")
    if type(gpu) is not dict or gpu.get("pci_id")!=R9700_PCI_ID or gpu.get("vendor")!="0x1002" or gpu.get("driver")!="amdgpu" or gpu.get("pdev")!=REQUIRED_PDEV:
        return _denied("discrete_gpu_identity_or_driver_missing")
    for key in ("vram_total","vram_used","gtt_total","gtt_used"):
        if not _positive_int(gpu.get(key)):
            return _denied("gpu_memory_counter_invalid_"+key)
    # The card reports slightly less than 32 GiB due to reserved regions.
    if not 31*1024**3<=gpu["vram_total"]<=33*1024**3:
        return _denied("unexpected_gpu_memory_capacity")
    if gpu["vram_used"]>gpu["vram_total"] or gpu["gtt_used"]>gpu["gtt_total"]:
        return _denied("gpu_memory_counter_out_of_range")
    samples=data.get("samples")
    if type(samples) is not list or len(samples)!=2:
        return _denied("missing_second_pressure_observation")
    for sample in samples:
        if type(sample) is not dict:
            return _denied("invalid_pressure_sample")
        for k in ("MemAvailable_KiB","SwapFree_KiB","MemTotal_KiB","SwapTotal_KiB"):
            if not _positive_int(sample.get(k)):
                return _denied("missing_memory_field_"+k)
        for k in ("memory_psi_avg10","cpu_psi_avg10","io_psi_avg10"):
            v=sample.get(k)
            if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=100:
                return _denied("invalid_pressure_"+k)
        if sample["MemAvailable_KiB"]>sample["MemTotal_KiB"] or sample["SwapFree_KiB"]>sample["SwapTotal_KiB"]:
            return _denied("overcommitted_source_counters")
    service=data.get("protected_lfm3")
    if type(service) is not dict:
        return _denied("protected_service_unidentified")
    expected_service={
        "model_basename":PINNED_BASENAME,
        "model_file_size_bytes":PINNED_SIZE,
        "host":"127.0.0.1",
        "port":"19175",
        "requested_device":"Vulkan1",
        "requested_gpu_layers":"99",
        "credential_file_flag_present":True,
        "health_http":200,
        "model_list_http":401,
    }
    for key,want in expected_service.items():
        if type(service.get(key)) is not type(want) or service[key]!=want:
            return _denied("protected_service_metadata_mismatch_"+key)
    before=service.get("before");after=service.get("after")
    if type(before) is not dict or type(after) is not dict:
        return _denied("gpu_client_bracketing_missing")
    for key in ("pid","process_start_ticks"):
        if (type(before.get(key)) is not int or before[key]<=0 or
                before.get(key)!=after.get(key)):
            return _denied("stale_or_replaced_protected_service")
    a=before.get("gpu_clients");b=after.get("gpu_clients")
    if type(a) is not list or type(b) is not list or len(a)!=1 or len(b)!=1:
        return _denied("missing_or_ambiguous_discrete_drm_client")
    client=a[0];nextclient=b[0]
    for k in ("pdev","client_id","driver","render_device"):
        if type(client.get(k)) is not str or client.get(k)!=nextclient.get(k):
            return _denied("gpu_client_identity_drift")
    if client["pdev"]!=REQUIRED_PDEV or client["driver"]!="amdgpu" or client["render_device"]!="/dev/dri/renderD128" or not client["client_id"].isdigit():
        return _denied("wrong_gpu_used_by_model")
    for obj in (client,nextclient):
        for key in ("resident_vram_kib","resident_gtt_kib","engine_compute_ns"):
            value=obj.get(key)
            if value is not None and not _positive_int(value):
                return _denied("malformed_drm_counter")
        if not _positive_int(obj.get("resident_vram_kib")) or not _positive_int(obj.get("resident_gtt_kib")):
            return _denied("missing_resident_gpu_memory_metrics")
        if obj["resident_gtt_kib"]+obj["resident_vram_kib"]<=0:
            return _denied("model_gpu_allocation_not_observed")
    if (client.get("engine_compute_ns") is not None and
        nextclient.get("engine_compute_ns") is not None and
        nextclient["engine_compute_ns"]<client["engine_compute_ns"]):
        return _denied("nonmonotonic_gpu_engine_counter")
    reasons=[]
    for i,s in enumerate(samples):
        if s["MemAvailable_KiB"]<NEW_MODEL_MIN_RAM_KIB:
            reasons.append("insufficient_system_ram_for_new_model")
        if s["SwapFree_KiB"]<NEW_MODEL_MIN_SWAP_KIB:
            reasons.append("insufficient_free_swap_for_new_model")
        if s["memory_psi_avg10"]>1 or s["cpu_psi_avg10"]>2 or s["io_psi_avg10"]>5:
            reasons.append("excessive_host_pressure")
    free_vram=gpu["vram_total"]-gpu["vram_used"]
    free_gtt=gpu["gtt_total"]-gpu["gtt_used"]
    if free_vram<NEW_MODEL_MIN_VRAM_BYTES:
        reasons.append("insufficient_free_vram")
    if free_gtt<NEW_MODEL_MIN_GTT_BYTES:
        reasons.append("insufficient_free_gtt")
    if data.get("rocm_cli_available") is not True:
        reasons.append("rocm_userland_cli_unavailable")
    if data.get("actual_model_sha256_checked_this_session") is not True:
        reasons.append("model_digest_not_rechecked_under_pressure")
    if data.get("signed_request_gpu_witness") is not True:
        reasons.append("independent_kernel_and_generation_custody_missing")
    reasons.extend(("protected_service_requires_credential",
                    "no_authorized_production_router_canary",
                    "live_gpu_utilization_and_speed_not_measured"))
    return {
        "decision":"PASS_HARDWARE_CONTEXT_HOLD_NEW_INFERENCE",
        "hardware_and_gpu_context_verified":True,
        "existing_protected_service_healthy":True,
        "gpu_pdev":REQUIRED_PDEV,
        "gpu_vram_total_mib":round(gpu["vram_total"]/1048576,1),
        "gpu_vram_free_mib":round(free_vram/1048576,1),
        "gpu_gtt_free_mib":round(free_gtt/1048576,1),
        "min_available_system_ram_mib":round(min(s["MemAvailable_KiB"] for s in samples)/1024,1),
        "min_free_swap_mib":round(min(s["SwapFree_KiB"] for s in samples)/1024,3),
        "protected_model_gpu_resident_vram_kib":nextclient["resident_vram_kib"],
        "protected_model_gpu_resident_gtt_kib":nextclient["resident_gtt_kib"],
        "new_model_canary_admitted":False,
        "protected_generation_authorized":False,
        "production_authorized":False,
        "independent_gpu_token_execution_attested":False,
        "live_gpu_saturation_measured":False,
        "reason_codes":sorted(set(reasons)),
    }
