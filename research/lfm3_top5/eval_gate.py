"""Fail-closed evaluation gate for synthetic or independently witnessed LFM3 trials."""
from dataclasses import dataclass
from math import isfinite
from typing import Mapping

METRICS=("format","exact_match","tool_schema","provenance_rejection","latency")
MIN_SAMPLES = 20
"""Minimum independent evaluation cases required per metric."""
@dataclass(frozen=True)
class Gate:
    decision: str
    reasons: tuple[str,...]
    fractions: dict[str,float]
    comparison_key: tuple[str,str]

def evaluate(report: Mapping, baseline: Mapping | None, *, min_rate=0.95,
             max_regression=0.02) -> Gate:
    if type(min_rate) not in (int,float) or not isfinite(min_rate) or not 0 <= min_rate <= 1:
        raise ValueError("invalid_min_rate")
    if type(max_regression) not in (int,float) or not isfinite(max_regression) or not 0 <= max_regression <= 1:
        raise ValueError("invalid_max_regression")
    if not isinstance(report, Mapping):
        return Gate("HOLD",("missing_report",),{},("",""))
    run_id=report.get("run_id")
    key=(report.get("workload_sha256"),report.get("checkpoint_sha256"))
    if not all(isinstance(x,str) and x for x in (*key,run_id)):
        return Gate("HOLD",("missing_identity",),{},("",""))
    reasons=[]
    if report.get("training_reuse") and report.get("training_opt_in") is not True:
        reasons.append("forbidden_training_reuse")
    vals={}
    cases=report.get("metrics")
    if not isinstance(cases,dict):
        return Gate("HOLD",("missing_metrics",),{},key)
    for k in METRICS:
        pair=cases.get(k)
        if not isinstance(pair,dict):
            reasons.append("missing_"+k)
            continue
        passed,total=pair.get("pass"),pair.get("total")
        if type(passed) is not int or type(total) is not int or total < 1 or not 0 <= passed <= total:
            reasons.append("invalid_"+k)
            continue
        if total < MIN_SAMPLES:
            reasons.append("insufficient_samples")
        vals[k]=passed/total
        if vals[k]<min_rate:
            reasons.append("below_floor_"+k)
    if baseline is None:
        reasons.append("missing_baseline")
    else:
        if not isinstance(baseline,Mapping) or baseline.get("workload_sha256")!=key[0]:
            reasons.append("baseline_workload_mismatch")
        else:
            # Compare candidate and frozen baseline on the same workload, while
            # permitting different checkpoint hashes (the normal upgrade case).
            bid=baseline.get("run_id")
            bmodel=baseline.get("checkpoint_sha256")
            if not isinstance(bid,str) or not bid or bid==run_id:
                reasons.append("baseline_run_not_independent")
            if not isinstance(bmodel,str) or not bmodel:
                reasons.append("baseline_checkpoint_missing")
            if baseline.get("frozen") is not True:
                reasons.append("baseline_not_frozen")
            if isinstance(baseline.get("metrics"),dict):
                for k,v in vals.items():
                    pair=baseline["metrics"].get(k)
                    if (isinstance(pair,dict) and type(pair.get("pass")) is int
                        and type(pair.get("total")) is int and pair["total"]>0
                        and 0<=pair["pass"]<=pair["total"]):
                        if pair["total"] < MIN_SAMPLES:
                            reasons.append("insufficient_samples")
                        base=pair["pass"]/pair["total"]
                        if v < base-max_regression:
                            reasons.append("regressed_"+k)
                    else:
                        reasons.append("baseline_missing_"+k)
            else:
                reasons.append("baseline_missing_metrics")
    if any(x.startswith(("below_floor_","regressed_","forbidden_training_reuse")) for x in reasons):
        return Gate("FAIL",tuple(sorted(reasons)),vals,key)
    if report.get("evidence_class")!="independently_witnessed_real" or report.get("exact_head_ci") is not True:
        reasons.append("research_only_not_release_evidence")
    if reasons:
        return Gate("HOLD",tuple(sorted(reasons)),vals,key)
    return Gate("PASS_TO_REVIEW",(),vals,key)

_WITNESS_HEX = frozenset("0123456789abcdef")

def _valid_witness_digest(value: object) -> bool:
    """True only for a lowercase 64-character hexadecimal digest string."""
    return (isinstance(value, str) and len(value) == 64
            and all(ch in _WITNESS_HEX for ch in value))

def _is_approved_collection(value: object) -> bool:
    """True for accepted approved-witness collections; fail closed otherwise."""
    return isinstance(value, (set, frozenset, list, tuple, dict))

def evaluate_witnessed(report: Mapping, baseline: Mapping | None, *,
                       witness_digest: str, approved_witnesses) -> Gate:
    """Fail-closed gate that additionally requires an approved external witness."""
    gate = evaluate(report, baseline)
    if gate.decision == "FAIL":
        # An existing FAIL is never masked or downgraded by witness state.
        return gate
    approved = (isinstance(witness_digest, str)
                and _valid_witness_digest(witness_digest)
                and _is_approved_collection(approved_witnesses)
                and witness_digest in approved_witnesses)
    if not approved:
        return Gate("HOLD", tuple(sorted({*gate.reasons, "witness_unavailable"})), gate.fractions, gate.comparison_key)
    # An existing PASS_TO_REVIEW with an approved witness may be permitted; an
    # existing HOLD (e.g. research-only evidence) is surfaced unchanged.
    return gate


_BINDING_FIELDS = (
    "candidate_run_id",
    "candidate_checkpoint_sha256",
    "baseline_run_id",
    "baseline_checkpoint_sha256",
    "workload_sha256",
)


def _binding_is_exact(metadata: object, report: Mapping, baseline: Mapping | None) -> bool:
    """True only when an approved binding matches the supplied report/baseline identities."""
    if not isinstance(metadata, Mapping) or not isinstance(report, Mapping):
        return False
    expected = {
        "candidate_run_id": report.get("run_id"),
        "candidate_checkpoint_sha256": report.get("checkpoint_sha256"),
        "baseline_run_id": baseline.get("run_id") if isinstance(baseline, Mapping) else None,
        "baseline_checkpoint_sha256": baseline.get("checkpoint_sha256") if isinstance(baseline, Mapping) else None,
        "workload_sha256": report.get("workload_sha256"),
    }
    for field in _BINDING_FIELDS:
        if field not in metadata or metadata[field] != expected[field]:
            return False
    return True


def evaluate_bound_witnessed(report: Mapping, baseline: Mapping | None, *,
                             witness_digest: str, approved_bindings) -> Gate:
    """Fail-closed gate that additionally requires an approved external witness.

    The witness digest must be a lowercase 64-hex digest and the approved binding
    for that digest must independently and exactly match the candidate and frozen
    baseline identities supplied in the report and baseline.  Only an exact
    matching independent binding may permit PASS_TO_REVIEW; FAIL is never masked and
    HOLD is never upgraded by approval alone.
    """
    gate = evaluate(report, baseline)
    if gate.decision == "FAIL":
        # An existing FAIL is never masked or downgraded by witness state.
        return gate
    approved = (
        isinstance(witness_digest, str)
        and _valid_witness_digest(witness_digest)
        and isinstance(approved_bindings, Mapping)
        and _binding_is_exact(approved_bindings.get(witness_digest), report, baseline)
    )
    if not approved:
        return Gate("HOLD", tuple(sorted({*gate.reasons, "witness_unavailable"})), gate.fractions, gate.comparison_key)
    # An existing PASS_TO_REVIEW with an exactly approved independent binding may be
    # permitted; an existing HOLD (e.g. research-only evidence) is surfaced unchanged.
    return gate
