"""Pure, non-deploying, quality-adjusted rate analysis of LFM3 fleet tasks.

NEVER route by raw tok/s alone. Strict exact model correctness and separately
source-validated, sorted node summaries are distinct result types.
The denominator is SUM of observed per-request HTTP wall durations, not
process startup, source verification, parsing time, network, queue time,
energy, concurrency, production success or cost. Rates are optimistic
research-only accounting ratios, not service throughput or SLOs.

The source snapshots in this frozen research fixture are caller-supplied and
must match the exact preregistered synthetic scenario; they are NOT remotely
attested/custodied inputs. This evaluator cannot approve production.
"""
from __future__ import annotations

import math
from statistics import median

from research.lfm3_top5.quality_task_audit import (
    MODES, ORACLES, assess_full_matrix, order, canonical,
)
from research.lfm3_top5.quality_canonical_guard import node_summary_guard, release_gate_guard

NODES={
    "x1-370":"PASS","xwing":"HOLD","joyner":"HOLD",
    "deathstar":"PASS","destroyer":"PASS","optiplex":"FAIL",
}
GATES={
    "source":"PASS","ci":"PASS","tls":"FAIL",
    "database":"FAIL","signer":"HOLD",
}
REQUIRED_NODES=tuple(sorted(NODES))
REQUIRED_GATES=tuple(sorted(GATES))

def _hold(reason):
    return {
        "decision":"HOLD",
        "reason":reason,
        "verified_request_count":0,
        "validated_rate_observations":None,
        "significant_performance_advantage_established":False,
        "independent_gpu_kernel_attestation":False,
        "source_custody_independently_signed":False,
        "free_provider_billing_verified":False,
        "production_authorized":False,
    }

def _number(x):
    return (type(x) in (int,float) and math.isfinite(x) and
            0 < x <= 22000)

def _metric(requests, task, *, source_nodes, source_gates):
    if not requests or len(requests)!=4:
        raise ValueError("incomplete_mode_task_cell")
    model_ms=sum(r["wall_ms"] for r in requests)
    if not _number(model_ms / 4):
        raise ValueError("invalid_inference_duration")
    count_raw_exact=sum(int(r["semantic_oracle_pass"]) for r in requests)
    def safe_cleaned(r):
        if task=="nodes":
            result=node_summary_guard(
                r["full_public_synthetic_answer"],
                source_snapshot=source_nodes,
                required_nodes=REQUIRED_NODES)
            return result["semantic_matches_source"] is True
        result=release_gate_guard(
            r["full_public_synthetic_answer"],
            source_gates=source_gates,
            required_gates=REQUIRED_GATES)
        return result["model_exactly_matches_source"] is True
    validated=sum(int(safe_cleaned(r)) for r in requests)
    # Never call a deterministic source-only gate evaluator a model success.
    count_after_guard=validated if task=="nodes" else 0
    # Sum observed HTTP durations; absent startup and guard time
    # means this is an optimistic research-only upper-bound ratio.
    raw_rps=count_raw_exact / (model_ms / 1000)
    verified_rps=count_after_guard / (model_ms / 1000)
    return {
        "requests":4,
        "all_responses_token_accounting_valid":True,
        "raw_exact_model_successes":count_raw_exact,
        "source_verified_model_node_memberships":validated if task=="nodes" else None,
        "model_exact_release_gate_fields":validated if task=="gate" else None,
        "source_grounded_node_canonical_successes":count_after_guard if task=="nodes" else None,
        "model_release_gate_successes_for_routing":0 if task=="gate" else None,
        "sum_observed_model_http_ms":round(model_ms,3),
        "median_observed_model_http_ms":round(median(r["wall_ms"] for r in requests),3),
        "median_native_decode_tokens_per_second":round(
            median(r["timings"]["predicted_per_second"] for r in requests),3),
        "strict_model_correct_per_observed_http_second":round(raw_rps,6),
        "source_verified_node_correct_per_observed_http_second_upper_bound":
            round(verified_rps,6) if task=="nodes" else None,
        "full_end_to_end_latency_with_validation_measured":False,
        "quality_denominator_includes_startup_or_validation":False,
    }

def assess_quality_adjusted(prereg, receipts, *,
                            source_nodes, source_gates):
    """Score frozen, exact synthetic use cases; fail closed on missing source.

    No result, including synthetic all-PASS, authorizes real model routing.
    """
    if (type(source_nodes) is not dict or type(source_gates) is not dict
        or source_nodes!=NODES or source_gates!=GATES):
        return _hold("source_snapshot_not_frozen_research_scenario")
    # Explicit full source, exact gate states and output constraints.
    node_expected=node_summary_guard(
        canonical(ORACLES["nodes"]["expected"]),
        source_snapshot=source_nodes,required_nodes=REQUIRED_NODES)
    gate_expected=release_gate_guard(
        canonical(ORACLES["gate"]["expected"]),
        source_gates=source_gates,required_gates=REQUIRED_GATES)
    if (node_expected["decision"]!="PASS_SEMANTIC_NORMALIZATION_RESEARCH_ONLY"
        or node_expected["canonical_output"]!=ORACLES["nodes"]["expected"]
        or gate_expected["canonical_output"]!=ORACLES["gate"]["expected"]
        or gate_expected["decision"]!="HOLD"
        or gate_expected["production_authorized"] is not False):
        return _hold("oracle_does_not_match_source")
    base=assess_full_matrix(prereg,receipts)
    if base.get("decision")!="PASS_SAFE_CAPTURE_WITH_EXACT_QUALITY_GATE_RESEARCH_ONLY":
        return _hold("unverified_underlying_receipts")
    # Use full underlying receipts rather than rounded per-block summaries.
    by_task={}
    for task in ("gate","nodes"):
        by_task[task]={}
        for mode in MODES:
            rows=[r for block in (1,2)
                  for r in receipts[(mode,block,task)]["requests"]]
            if any(not _number(r.get("wall_ms")) for r in rows):
                return _hold("invalid_unrounded_request_wall_time")
            try:
                by_task[task][mode]=_metric(
                    rows,task,source_nodes=source_nodes,source_gates=source_gates)
            except (ValueError,TypeError,KeyError,OverflowError,ZeroDivisionError):
                return _hold("quality_adjustment_error")
    if any(by_task[task][mode]["raw_exact_model_successes"] !=
           base["per_task"][task][mode]["exact_oracle_passes"]
           for task in by_task for mode in MODES):
        return _hold("recomputed_accuracy_inconsistent_with_base_audit")
    return {
        "decision":"PASS_QUALITY_ADJUSTED_DESCRIPTIVE_RESEARCH_ONLY",
        "verified_request_count":24,
        "raw_model_exact_successes":sum(
            by_task[task][mode]["raw_exact_model_successes"]
            for task in by_task for mode in MODES),
        "quality_adjusted_task_rates":by_task,
        "source_derived_release_gate_decision":"HOLD",
        "source_derived_release_gate_independent_of_model":True,
        "model_release_gate_decisions_authoritative":False,
        "metric_denominator":"sum_sequential_observed_model_HTTP_wall_seconds_only",
        "denominator_excludes_process_start_and_rollback":True,
        "denominator_excludes_source_validation_and_energy":True,
        "rate_is_optimistic_research_only_upper_bound":True,
        "inter_mode_sample_size_per_task":4,
        "quality_adjusted_routing_candidate_admitted":False,
        "significant_performance_advantage_established":False,
        "independent_gpu_kernel_attestation":False,
        "cpu_zero_gpu_activity_verified":False,
        "source_custody_independently_signed":False,
        "free_provider_billing_verified":False,
        "production_authorized":False,
    }
