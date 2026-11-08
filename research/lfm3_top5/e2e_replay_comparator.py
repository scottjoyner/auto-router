"""Offline, source-grounded replay comparison for LFM3 fleet operations.

For a frozen public synthetic scenario, compare:
  (1) deterministic source-only computation; and
  (2) post-processing historical model responses with a fail-closed guard.

Clock samples measure LOCAL validation work NOW. Historical model HTTP
durations come from a DIFFERENT Deathstar execution session. Summing them
gives a RECONSTRUCTED accounting proxy, NOT observed live end-to-end
latency, throughput, production SLO, power, billing or kernel attestation.
No inference is executed by this module. No route or release is authorized.
"""
from __future__ import annotations

import hashlib
import math
import random
import time
from statistics import median

from research.lfm3_top5.quality_task_audit import ORACLES, MODES, assess_full_matrix, canonical, order
from research.lfm3_top5.quality_adjusted_eval import NODES, GATES
from research.lfm3_top5.quality_canonical_guard import node_summary_guard, release_gate_guard

REPLAY_SEED = 202611081547
MAX_TRIALS = 512

def hold(reason):
    return {
        "decision": "HOLD",
        "reason": reason,
        "performance_sample_count": 0,
        "source_independently_attested": False,
        "kernel_execution_independently_attested": False,
        "quality_eligible_for_production_routing": False,
        "production_authorized": False,
    }

def _source_only(task, nodes, gates):
    if task == "nodes":
        expected = {
            "healthy": sorted(n for n, s in nodes.items() if s == "PASS"),
            "hold_or_fail": sorted(n for n, s in nodes.items() if s != "PASS"),
            "total": len(nodes),
        }
        result = node_summary_guard(
            canonical(expected), source_snapshot=nodes, required_nodes=tuple(sorted(NODES))
        )
        good = (
            result.get("semantic_matches_source") is True
            and result.get("canonical_output") == ORACLES["nodes"]["expected"]
        )
        return {"valid": good, "output": result.get("canonical_output") if good else None}
    if task == "gate":
        result = release_gate_guard(
            "{}", source_gates=gates, required_gates=tuple(sorted(GATES))
        )
        good = (
            result.get("decision") == "HOLD"
            and result.get("canonical_output") == ORACLES["gate"]["expected"]
        )
        return {"valid": good, "output": result.get("canonical_output") if good else None}
    return {"valid": False, "output": None}

def _model_assisted(task, text, nodes, gates):
    # Even a plausible model gate string is advisory. The source itself
    # determines the canonical HOLD release decision.
    if task == "nodes":
        result = node_summary_guard(
            text, source_snapshot=nodes, required_nodes=tuple(sorted(NODES))
        )
        good = result.get("semantic_matches_source") is True
        output = result.get("canonical_output") if good else None
        return {
            "model_semantic_accepted": good,
            "source_result_valid": good and output == ORACLES["nodes"]["expected"],
            "output": output,
            "model_release_authority": False,
        }
    if task == "gate":
        result = release_gate_guard(
            text, source_gates=gates, required_gates=tuple(sorted(GATES))
        )
        correct = result.get("model_exactly_matches_source") is True
        canonical_result = _source_only(task, nodes, gates)
        return {
            "model_semantic_accepted": correct,
            "source_result_valid": canonical_result["valid"],
            "output": canonical_result["output"],
            "model_release_authority": False,
        }
    return {
        "model_semantic_accepted": False,
        "source_result_valid": False,
        "output": None,
        "model_release_authority": False,
    }

def _clock_run(fn, clock_ns):
    start = clock_ns()
    result = fn()
    end = clock_ns()
    if type(start) is not int or type(end) is not int or not 0 < end - start <= 30_000_000_000:
        raise ValueError("invalid_validation_timer")
    return result, end - start

def _stats(values_ns):
    if not values_ns or any(type(x) is not int or x <= 0 for x in values_ns):
        raise ValueError("invalid_timing_samples")
    seq = sorted(values_ns)
    p95 = seq[max(0, math.ceil(.95 * len(seq)) - 1)]
    return {
        "samples": len(seq),
        "median_microseconds": round(median(seq) / 1000, 3),
        "p95_microseconds": round(p95 / 1000, 3),
        "max_microseconds": round(seq[-1] / 1000, 3),
    }

def compare_replay(
    prereg, receipts, *,
    source_nodes, source_gates,
    repeats=32, warmup=3,
    seed=REPLAY_SEED, clock_ns=None
):
    """Deterministic order for LOCAL replay only, never a randomized live study.

    The reference scenario must be exact and complete, or the entire
    comparison HOLDs. Does not count release gate source-only output as
    success attributable to the model.
    """
    if (
        type(source_nodes) is not dict or source_nodes != NODES
        or type(source_gates) is not dict or source_gates != GATES
    ):
        return hold("missing_or_changed_frozen_source")
    if (
        type(repeats) is not int or not 1 <= repeats <= MAX_TRIALS
        or type(warmup) is not int or not 0 <= warmup <= 32
        or type(seed) is not int or not 0 <= seed < 2**63
    ):
        return hold("invalid_replay_sampling_configuration")
    base = assess_full_matrix(prereg, receipts)
    if base.get("decision") != "PASS_SAFE_CAPTURE_WITH_EXACT_QUALITY_GATE_RESEARCH_ONLY":
        return hold("unverified_input_receipts")
    clock_ns = time.perf_counter_ns if clock_ns is None else clock_ns
    if not callable(clock_ns):
        return hold("clock_not_callable")

    # The recorded model request identity and answer bytes are independently
    # re-audited in the base assessment. Build one row for each actual
    # request, but do not write their process IDs or model text to the report.
    cases = []
    for mode, block, task in order():
        for row in receipts[(mode, block, task)]["requests"]:
            elapsed = row["wall_ms"]
            answer = row["full_public_synthetic_answer"]
            if (
                type(elapsed) not in (int, float) or not math.isfinite(elapsed)
                or not 0 < elapsed <= 22000 or type(answer) is not str
            ):
                return hold("bad_historical_model_duration_or_text")
            cases.append((mode, task, answer, elapsed))

    if len(cases) != 24:
        return hold("wrong_request_count")

    groups = {(task, mode): {
        "source_ns": [], "assisted_ns": [],
        "historical_http_ms": [], "source_ok": 0,
        "model_membership_ok": 0, "model_full_exact_ok": 0
    } for task in ("nodes", "gate") for mode in MODES}

    # Fixed seed shuffles local replay, not retrospective model trials.
    rng = random.Random(seed)
    try:
        for round_index in range(warmup + repeats):
            positions = list(range(len(cases)))
            rng.shuffle(positions)
            for i in positions:
                mode, task, answer, elapsed = cases[i]
                src, src_ns = _clock_run(
                    lambda: _source_only(task, source_nodes, source_gates), clock_ns)
                assisted, assisted_ns = _clock_run(
                    lambda: _model_assisted(task, answer, source_nodes, source_gates),
                    clock_ns)
                # A wrong model answer is a valid ZERO-quality observation;
                # it must NOT be relabeled as corrupt measurement evidence.
                # By contrast, an untrusted or incomplete source fails
                # closed and prevents any benchmark quality claim.
                if (src["valid"] is not True
                    or src["output"] != ORACLES[task]["expected"]
                    or assisted["model_release_authority"] is not False):
                    return hold("source_only_integrity_or_model_authority_broken")
                if task == "gate":
                    if (assisted["source_result_valid"] is not True
                        or assisted["output"] != ORACLES["gate"]["expected"]):
                        return hold("source_gate_derivation_broken")
                elif assisted["model_semantic_accepted"]:
                    if (assisted["source_result_valid"] is not True
                        or assisted["output"] != ORACLES["nodes"]["expected"]):
                        return hold("validated_model_node_canonicalization_broken")
                elif assisted["output"] is not None:
                    return hold("invalid_model_node_result_was_admitted")
                if round_index >= warmup:
                    g = groups[(task, mode)]
                    g["source_ns"].append(src_ns)
                    g["assisted_ns"].append(assisted_ns)
                    # The same historical 4 request durations are reused
                    # each round, not treated as independent samples.
                    if round_index == warmup:
                        g["historical_http_ms"].append(elapsed)
                        g["source_ok"] += 1
                        g["model_membership_ok"] += int(assisted["model_semantic_accepted"])
                        g["model_full_exact_ok"] += int(
                            receipts_exact_for_task(task, answer))
    except Exception:
        return hold("local_replay_error")

    results = {}
    for task in ("nodes", "gate"):
        results[task] = {}
        for mode in MODES:
            g = groups[(task, mode)]
            if (
                len(g["historical_http_ms"]) != 4
                or len(g["source_ns"]) != repeats * 4
                or len(g["assisted_ns"]) != repeats * 4
                or g["source_ok"] != 4
            ):
                return hold("missing_group_data")
            direct = _stats(g["source_ns"])
            guarded = _stats(g["assisted_ns"])
            # This is not a live E2E sample: two different sessions/hosts.
            reconstructed_proxy_ms = sum(g["historical_http_ms"]) + (
                4 * guarded["median_microseconds"] / 1000
            )
            results[task][mode] = {
                "historical_http_request_count": 4,
                "historical_http_total_ms": round(sum(g["historical_http_ms"]), 3),
                "source_only_local_validation": direct,
                "model_assisted_local_validation": guarded,
                "reconstructed_model_plus_guard_total_ms_proxy": round(
                    reconstructed_proxy_ms, 3),
                "reconstruction_is_NOT_live_e2e_latency": True,
                "strict_model_exact_successes": g["model_full_exact_ok"],
                "source_only_correct_results": g["source_ok"],
                "model_source_verified_memberships": (
                    g["model_membership_ok"] if task == "nodes" else None),
                "model_release_gate_exact_correct": (
                    g["model_full_exact_ok"] if task == "gate" else None),
                "model_contribution_required_for_source_only_success": False,
                "production_authorized": False,
            }

    return {
        "decision": "PASS_OFFLINE_TWO_PATH_REPLAY_RESEARCH_ONLY",
        "performance_sample_count": repeats * 24 * 2,
        "historical_unique_requests": 24,
        "per_case_validation_trials": repeats,
        "local_replay_warmup_rounds": warmup,
        "local_replay_seed": seed,
        "source_only_and_assisted_replay": results,
        "source_only_requires_model_call": False,
        "release_gate_result_derived_from_model": False,
        "verified_gate_decision": "HOLD",
        "historical_http_and_local_validation_measured_in_same_session": False,
        "process_startup_included": False,
        "source_provenance_verification_included": False,
        "energy_cost_or_concurrent_service_load_included": False,
        "full_end_to_end_latency_measured": False,
        "benchmark_can_prove_model_improves_deterministic_task": False,
        "source_independently_attested": False,
        "kernel_execution_independently_attested": False,
        "quality_eligible_for_production_routing": False,
        "production_authorized": False,
    }

def receipts_exact_for_task(task, answer):
    # The preregistered FULL raw-object oracle; not source-corrected output.
    from research.lfm3_top5.quality_task_audit import exact_task_oracle
    return exact_task_oracle(task, answer)
