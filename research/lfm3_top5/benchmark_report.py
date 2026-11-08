"""Pure synthetic fleet LFM3 benchmark aggregator; never triggers inference."""
from collections import defaultdict
from dataclasses import dataclass
from math import isfinite, ceil
from statistics import median

FIELDS=("workload_sha256","checkpoint_sha256","quantization","context_tokens","max_output_tokens","sampling_hash")
@dataclass(frozen=True)
class Summary:
    comparison_key: tuple
    node: str
    observations: int
    prefill_tokens_per_sec_p50: float
    decode_tokens_per_sec_p50: float
    decode_tokens_per_sec_p95: float
    peak_ram_mb: float
    accelerator_verified: bool

def _positive(value):
    return type(value) in (int,float) and isfinite(value) and value > 0

def summarize(rows, verified_accelerator_digests=()):
    groups=defaultdict(list)
    for row in rows:
        if not isinstance(row,dict):
            raise ValueError("malformed_observation")
        key=tuple(row.get(k) for k in FIELDS)
        if any(type(row.get(k)) is not str or not row[k].strip()
               for k in ("workload_sha256","checkpoint_sha256","quantization","sampling_hash","node")):
            raise ValueError("missing_comparison_identity")
        for k in ("context_tokens","max_output_tokens","prompt_tokens","output_tokens"):
            if type(row.get(k)) is not int or row[k] < 1:
                raise ValueError("invalid_"+k)
        for k in ("prefill_ms","decode_ms","peak_ram_mb"):
            if not _positive(row.get(k)):
                raise ValueError("invalid_"+k)
        node=row["node"]
        prefill=1000*row["prompt_tokens"]/row["prefill_ms"]
        decode=1000*row["output_tokens"]/row["decode_ms"]
        groups[(key,node)].append((prefill,decode,row["peak_ram_mb"],
                                    row.get("accelerator_verified") is True
                                    and isinstance(row.get("accelerator_backend_evidence_sha256"),str)
                                    and len(row["accelerator_backend_evidence_sha256"])==64
                                    and all(c in "0123456789abcdef" for c in row["accelerator_backend_evidence_sha256"])
                                    and row["accelerator_backend_evidence_sha256"] in verified_accelerator_digests))
    out=[]
    for (key,node),group in sorted(groups.items()):
        pref=sorted(v[0] for v in group)
        dec=sorted(v[1] for v in group)
        out.append(Summary(key,node,len(group),round(median(pref),3),
                           round(median(dec),3),round(dec[ceil(len(dec)*0.95)-1],3),
                           max(v[2] for v in group),all(v[3] for v in group)))
    return out

def comparable(a: Summary,b: Summary):
    return a.comparison_key==b.comparison_key


def _observation_id_problem(rows):
    seen=set()
    for row in rows:
        if not isinstance(row,dict):
            return "malformed_rows"
        observation_id=row.get("observation_id")
        if not isinstance(observation_id,str) or not observation_id.strip():
            return "missing_or_invalid_observation_id"
        if observation_id in seen:
            return "duplicate_observation_id"
        seen.add(observation_id)
    return None


def assess_repeatability(rows, *, min_samples=5, verified_accelerator_digests=()):
    """Pure advisory gate for at least N matched observations on ONE node.

    Workload, checkpoint, quantization, context, max output, sampling hash,
    and node must match. p50/p95 describe the input only; not a production SLO.
    """
    if type(min_samples) is not int or not 5 <= min_samples <= 100_000:
        return {"decision": "HOLD", "reason": "invalid_sample_floor",
                "observations": 0, "accelerator_verified": False}
    if not isinstance(rows, (list, tuple)):
        return {"decision": "HOLD", "reason": "malformed_rows",
                "observations": 0, "accelerator_verified": False}
    problem=_observation_id_problem(rows)
    if problem is not None:
        return {"decision": "HOLD", "reason": problem,
                "observations": 0, "accelerator_verified": False}
    try:
        summaries = summarize(rows,
            verified_accelerator_digests=verified_accelerator_digests)
    except (TypeError, ValueError, OverflowError, ZeroDivisionError):
        return {"decision": "HOLD", "reason": "malformed_observations",
                "observations": 0, "accelerator_verified": False}
    if len(summaries) != 1:
        return {"decision": "HOLD", "reason": "mixed_or_empty_cohort",
                "observations": len(rows), "accelerator_verified": False}
    result = summaries[0]
    if result.observations < min_samples:
        return {"decision": "HOLD", "reason": "insufficient_paired_samples",
                "observations": result.observations, "accelerator_verified": False}
    return {
        "decision": "PASS_RESEARCH_ONLY",
        "reason": "one_frozen_cohort_only",
        "observations": result.observations,
        "node": result.node,
        "comparison_key": result.comparison_key,
        "prefill_tokens_per_sec_p50": result.prefill_tokens_per_sec_p50,
        "decode_tokens_per_sec_p50": result.decode_tokens_per_sec_p50,
        "decode_tokens_per_sec_p95": result.decode_tokens_per_sec_p95,
        "accelerator_verified": result.accelerator_verified,
    }


def verify_local_stage_bundle(bundle, *, expected_checkpoint_sha256):
    """Check five CPU-only local-stage receipts against separately pinned GGUF digest.

    Does not authenticate server IDs, checkpoint custody, timing hardware or
    external witnesses. PASS_RESEARCH_ONLY means the bounded *provided* report
    is internally consistent; it never authorizes routing or claims GPU/NPU.
    """
    held = {"decision": "HOLD", "reason": "unverified_local_stage_bundle",
            "observations": 0, "production_authorized": False,
            "accelerator_verified": False}
    def digest(value):
        return (type(value) is str and len(value) == 64
                and all(char in "0123456789abcdef" for char in value))

    if (not isinstance(bundle, dict)
            or not digest(expected_checkpoint_sha256)
            or bundle.get("checkpoint_sha256") != expected_checkpoint_sha256
            or not digest(bundle.get("workload_sha256"))
            or bundle.get("schema") != "lfm3.real.cpu-stage.5x.v1"
            or bundle.get("scope") != "loopback-only-public-synthetic-prompt"
            or bundle.get("result") != "PASS_RESEARCH_ONLY"
            or bundle.get("backend") != "llama.cpp CPU-only -ngl 0"
            or type(bundle.get("model")) is not str or not bundle["model"].strip()
            or type(bundle.get("model_bytes")) is not int
            or bundle["model_bytes"] <= 1_000_000_000
            or type(bundle.get("requested_generations")) is not int
            or bundle["requested_generations"] != 5
            or type(bundle.get("actual_generations_attempted")) is not int
            or bundle["actual_generations_attempted"] != 5
            or bundle.get("production_authorized") is not False
            or bundle.get("accelerator_verified") is not False
            or bundle.get("independent_measurement_witness") is not False):
        return held
    rows, meta = bundle.get("rows"), bundle.get("request_meta")
    if (type(rows) is not list or type(meta) is not list
            or len(rows) != 5 or len(meta) != 5):
        return held
    seen = set()
    native_prefill_rates = []
    native_decode_rates = []
    for row, observation in zip(rows, meta):
        if type(row) is not dict or type(observation) is not dict:
            return held
        identifier = observation.get("observation_id")
        if (type(identifier) is not str or not identifier.strip()
                or identifier in seen or row.get("observation_id") != identifier):
            return held
        seen.add(identifier)
        if (observation.get("finish_reason") != "stop"
                or observation.get("answer") != "READY"
                or observation.get("model") != bundle["model"]
                or row.get("checkpoint_sha256") != expected_checkpoint_sha256
                or row.get("workload_sha256") != bundle["workload_sha256"]
                or row.get("sampling_hash") != bundle["workload_sha256"]
                or row.get("quantization") != "Q4_K_M"
                or row.get("context_tokens") != 4096
                or row.get("max_output_tokens") != 24
                or row.get("accelerator_verified") is not False
                or not _positive(observation.get("end_to_end_ms"))):
            return held
        usage = observation.get("usage")
        timings = observation.get("server_timings")
        if not isinstance(usage, dict) or not isinstance(timings, dict):
            return held
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if type(usage.get(key)) is not int or usage[key] < 1:
                return held
        for key in ("prompt_n", "predicted_n"):
            if type(timings.get(key)) is not int or timings[key] < 1:
                return held
        if (usage["prompt_tokens"] + usage["completion_tokens"] != usage["total_tokens"]
                or usage["completion_tokens"] != timings["predicted_n"]
                or usage["prompt_tokens"] < timings["prompt_n"]
                or row.get("prompt_tokens") != timings["prompt_n"]
                or row.get("output_tokens") != timings["predicted_n"]):
            return held
        for native_key, sink in (("prompt_per_second", native_prefill_rates),
                                 ("predicted_per_second", native_decode_rates)):
            if not _positive(timings.get(native_key)):
                return held
            sink.append(timings[native_key])
        for key, row_field in (("prompt_ms", "prefill_ms"),
                               ("predicted_ms", "decode_ms")):
            if (not _positive(timings.get(key))
                    or type(row.get(row_field)) not in (int, float)
                    or not _positive(row[row_field])
                    or row[row_field] != timings[key]):
                return held
    result = assess_repeatability(rows)
    if result["decision"] != "PASS_RESEARCH_ONLY" or result["accelerator_verified"]:
        return held
    return {"decision": "PASS_RESEARCH_ONLY",
            "reason": "five_internal_cpu_receipts_checked_only",
            "observations": 5, "checkpoint_sha256": expected_checkpoint_sha256,
            "production_authorized": False, "accelerator_verified": False,
            "independent_measurement_witness": False,
            "server_reported_prefill_tps_p50": round(median(native_prefill_rates),3),
            "server_reported_decode_tps_p50": round(median(native_decode_rates),3),
            "benchmark_summary": result}


def classify_existing_gpu_context(evidence):
    """Classify passive LFM3 GPU-client data WITHOUT authorizing acceleration.

    Actual LFM3 GPU token execution requires an authenticated, correlated
    generation witness, not a process flag, accumulated DRM counter, or a
    local file supplied by the process owner. This is an offline pure checker.
    """
    import re

    result = {
        "decision": "HOLD",
        "reason": "missing_stable_gpu_context",
        "gpu_context_observed": False,
        "historical_compute_engine_time_observed": False,
        "compute_delta_ns": 0,
        "accelerator_verified": False,
        "production_authorized": False,
    }

    def parse_ns(value):
        if type(value) is not str or re.fullmatch(r"[0-9]{1,22} ns", value) is None:
            return None
        number = int(value[:-3])
        return number if 0 <= number < 2**63 else None

    def parse_vram(value):
        if type(value) is not str:
            return None
        match = re.fullmatch(r"([0-9]{1,10}) (KiB|MiB)", value)
        if not match:
            return None
        return int(match.group(1)) * (1024 if match.group(2) == "MiB" else 1)

    if type(evidence) is not dict:
        return result
    if evidence.get("schema") != "lfm3.joyner.gpu-process.read-only.v1":
        return result
    if evidence.get("hostname") != "joyner":
        return result
    for authority_claim in (
        "production_authorized",
        "backend_dispatch_correlated_to_tokens",
        "authenticated_generation_receipt",
        "model_checkpoint_sha256_verified",
        "independent_gpu_execution_witness",
    ):
        if evidence.get(authority_claim) is not False:
            return result
    before, after = evidence.get("before"), evidence.get("after")
    if type(before) is not dict or type(after) is not dict:
        return result
    fields = ("pid", "process_starttime_ticks", "model_basename",
              "alias", "port", "host", "gpu_layers_requested")
    if any(before.get(field) != after.get(field) for field in fields):
        return result
    if (type(before.get("pid")) is not int or before["pid"] < 1
            or type(before.get("process_starttime_ticks")) is not int
            or before["process_starttime_ticks"] < 1
            or type(before.get("model_basename")) is not str
            or not before["model_basename"].endswith(".gguf")
            or "/" in before["model_basename"]
            or type(before.get("alias")) is not str or not before["alias"].strip()
            or before.get("host") != "127.0.0.1"
            or before.get("port") != "19175"
            or before.get("gpu_layers_requested") != "99"):
        return result
    first = before.get("drm_clients")
    second = after.get("drm_clients")
    if type(first) is not list or type(second) is not list:
        return result
    if not 1 <= len(first) <= 8 or not 1 <= len(second) <= 8:
        return result

    def find_discrete(clients):
        found = []
        for client in clients:
            if type(client) is not dict:
                return None
            device, metrics = client.get("device"), client.get("metrics")
            if type(device) is not str or type(metrics) is not dict:
                return None
            if (device != "/dev/dri/renderD128"
                    or metrics.get("drm-pdev") != "0000:03:00.0"):
                continue
            if (metrics.get("drm-driver") != "amdgpu"
                    or type(metrics.get("drm-client-id")) is not str
                    or not metrics["drm-client-id"].isdigit()):
                return None
            vram = parse_vram(metrics.get("drm-resident-vram"))
            counter = parse_ns(metrics.get("drm-engine-compute"))
            if vram is None or vram <= 0 or counter is None:
                return None
            found.append((metrics["drm-client-id"], vram, counter))
        return found[0] if len(found) == 1 else None

    a = find_discrete(first)
    b = find_discrete(second)
    if a is None or b is None or a[0] != b[0] or b[2] < a[2]:
        return result
    result["gpu_context_observed"] = True
    result["historical_compute_engine_time_observed"] = a[2] > 0 or b[2] > 0
    result["compute_delta_ns"] = b[2] - a[2]
    result["reason"] = "gpu_context_present_unproven_tokens"
    return result


def assess_gpu_request_correlation(bundle, *, expected_checkpoint_sha256):
    """Identify an *untrusted* one-request GPU correlation candidate.

    All inputs are caller-supplied, unsigned, and not independently witnessed.
    Even when temporal evidence matches a stable GPU process and usage receipt,
    this function *always* returns HOLD, never verified acceleration/admission.
    """
    result = {
        "decision": "HOLD",
        "reason": "missing_or_untrusted_gpu_request",
        "gpu_context_observed": False,
        "bounded_request_contract_satisfied": False,
        "temporal_gpu_activity_candidate": False,
        "compute_delta_ns": 0,
        "accelerator_verified": False,
        "production_authorized": False,
    }

    def is_hash(value):
        return (type(value) is str and len(value) == 64
                and all(char in "0123456789abcdef" for char in value))

    if type(bundle) is not dict or not is_hash(expected_checkpoint_sha256):
        return result
    context, receipt = bundle.get("context"), bundle.get("request")
    if type(context) is not dict or type(receipt) is not dict:
        return result
    passive = classify_existing_gpu_context(context)
    result["gpu_context_observed"] = passive["gpu_context_observed"]
    result["compute_delta_ns"] = passive["compute_delta_ns"]
    if not passive["gpu_context_observed"]:
        result["reason"] = "unstable_or_missing_gpu_process"
        return result
    before = context["before"]
    usage = receipt.get("usage")
    if type(usage) is not dict:
        return result
    if (receipt.get("schema") != "lfm3.joyner.one-request.correlation.v1"
            or receipt.get("model") != before["alias"]
            or receipt.get("checkpoint_sha256") != expected_checkpoint_sha256
            or type(receipt.get("server_request_id")) is not str
            or not 1 <= len(receipt["server_request_id"]) <= 128
            or receipt["server_request_id"].strip() != receipt["server_request_id"]
            or any(ch.isspace() for ch in receipt["server_request_id"])
            or type(receipt.get("server_pid")) is not int
            or receipt["server_pid"] != before["pid"]
            or type(receipt.get("process_starttime_ticks")) is not int
            or receipt["process_starttime_ticks"] != before["process_starttime_ticks"]
            or type(receipt.get("generations_attempted")) is not int
            or receipt["generations_attempted"] != 1
            or type(receipt.get("requested_max_tokens")) is not int
            or not 1 <= receipt["requested_max_tokens"] <= 24
            or receipt.get("finish_reason") != "stop"
            or receipt.get("response_text") != "READY"
            or receipt.get("credentials_retained") is not False
            or receipt.get("externally_authenticated_witness") is not False):
        return result
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if type(usage.get(key)) is not int or usage[key] < 1:
            return result
    if (usage["prompt_tokens"] + usage["completion_tokens"] != usage["total_tokens"]
            or usage["completion_tokens"] > receipt["requested_max_tokens"]):
        return result
    keys=("collector_before_ns", "request_started_ns",
          "request_finished_ns", "collector_after_ns")
    stamps=[receipt.get(key) for key in keys]
    if (any(type(value) is not int or value <= 0 or value >= 2**63
            for value in stamps)
            or not all(a < b for a,b in zip(stamps, stamps[1:]))
            or stamps[-1] - stamps[0] > 10_000_000_000):
        return result
    result["bounded_request_contract_satisfied"] = True
    result["temporal_gpu_activity_candidate"] = result["compute_delta_ns"] > 0
    result["reason"] = (
        "candidate_only_unsigned_uncorrelated_custody"
        if result["temporal_gpu_activity_candidate"]
        else "no_gpu_engine_change_during_request"
    )
    return result


def assess_matched_qad_cpu_bundle(bundle, *, expected_checkpoint_sha256):
    """Check five local CPU QAD responses and ephemeral-server cleanup.

    This is an *internal consistency check* on unsigned local observations,
    not an independent model, timing, GPU or custody witness. It can only
    return PASS_RESEARCH_ONLY or HOLD, never permission to deploy.
    """
    held={"decision":"HOLD","reason":"untrusted_or_incomplete_qad_cpu_bundle",
          "observations":0,"accelerator_verified":False,
          "production_authorized":False}
    def digest(value):
        return (type(value) is str and len(value)==64
                and all(c in "0123456789abcdef" for c in value))
    if (type(bundle) is not dict or not digest(expected_checkpoint_sha256)
            or bundle.get("schema")!="lfm3.x1.same-qad.cpu-five.v1"
            or bundle.get("node")!="x1-370"
            or bundle.get("model")!="LFM3-1.7B-QAD-Q4_0.gguf"
            or bundle.get("new_service_binding")!="127.0.0.1:19183"
            or bundle.get("expected_sha256")!=expected_checkpoint_sha256
            or bundle.get("observed_sha256")!=expected_checkpoint_sha256
            or type(bundle.get("model_bytes")) is not int
            or bundle["model_bytes"]!=1049615680
            or type(bundle.get("gpu_layers_requested")) is not int
            or bundle["gpu_layers_requested"]!=0
            or type(bundle.get("requested_generations")) is not int
            or bundle["requested_generations"]!=5
            or type(bundle.get("attempted_generations")) is not int
            or bundle["attempted_generations"]!=5
            or bundle.get("outcome")!="PASS_RESEARCH_ONLY"
            or bundle.get("child_stopped") is not True
            or bundle.get("port_available_after_cleanup") is not True
            or bundle.get("accelerator_verified") is not False
            or bundle.get("production_authorized") is not False
            or bundle.get("independent_measurement_witness") is not False):
        return held
    for key,minimum in (("mem_available_kib_before",8*1024*1024),
                        ("mem_available_kib_after",5*1024*1024)):
        value=bundle.get(key)
        if type(value) is not int or value<minimum:
            return held
    for key in ("memory_psi_some_avg10_before",
                "memory_psi_some_avg10_after"):
        value=bundle.get(key)
        if (type(value) not in (int,float) or not isfinite(value)
                or value<0 or value>0.2):
            return held
    rows=bundle.get("observations")
    if type(rows) is not list or len(rows)!=5:return held
    ids=set()
    native_decode=[]
    native_prefill=[]
    latencies=[]
    for row in rows:
        if type(row) is not dict:return held
        rid=row.get("observation_id")
        if (type(rid) is not str or not 1<=len(rid)<=128
                or rid.strip()!=rid or any(c.isspace() for c in rid)
                or rid in ids):
            return held
        ids.add(rid)
        if row.get("finish_reason")!="stop" or row.get("answer")!="READY":
            return held
        elapsed=row.get("end_to_end_ms")
        if not _positive(elapsed):return held
        usage=row.get("usage")
        timings=row.get("server_timings")
        if type(usage) is not dict or type(timings) is not dict:return held
        for key in ("prompt_tokens","completion_tokens","total_tokens"):
            if type(usage.get(key)) is not int or usage[key]<1:return held
        if usage["total_tokens"]!=usage["prompt_tokens"]+usage["completion_tokens"]:
            return held
        if usage["completion_tokens"]>24:return held
        for key in ("prompt_n","predicted_n"):
            if type(timings.get(key)) is not int or timings[key]<1:
                return held
        if (usage["completion_tokens"]!=timings["predicted_n"]
                or usage["prompt_tokens"]<timings["prompt_n"]):
            return held
        for key in ("prompt_ms","predicted_ms",
                    "prompt_per_second","predicted_per_second"):
            if not _positive(timings.get(key)):return held
        native_prefill.append(timings["prompt_per_second"])
        native_decode.append(timings["predicted_per_second"])
        latencies.append(elapsed)
    return {"decision":"PASS_RESEARCH_ONLY",
            "reason":"five_matching_qad_cpu_receipts_and_cleanup_reported",
            "observations":5,
            "checkpoint_sha256":expected_checkpoint_sha256,
            "server_native_decode_tps_p50":round(median(native_decode),3),
            "server_native_prefill_tps_p50":round(median(native_prefill),3),
            "end_to_end_ms_p50":round(median(latencies),3),
            "accelerator_verified":False,
            "independent_measurement_witness":False,
            "production_authorized":False}
