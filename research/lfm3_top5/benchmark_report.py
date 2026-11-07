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
