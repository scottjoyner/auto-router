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
