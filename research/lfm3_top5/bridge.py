"""Read-only aggregation of LFM3 research evidence across five priority lanes.

Advisory stage for reproducible local research. No production authorization.
No HTTP requests, subprocesses, filesystem mutations, model training, or routing.
All evidence inputs must be independently validated before any external promotion.
"""
from __future__ import annotations

from typing import Mapping, Callable
from .inference_contract import Sample
from .route_guard import verify_delegation
from .context_guard import build_verified_context
from .benchmark_report import assess_repeatability
from .eval_gate import evaluate_bound_witnessed


def build_lfm3_research_advisory(
    *,
    stage_sample: Sample,
    model_id: str,
    child_sessions: int,
    parent_receipt: Mapping,
    child_receipt: Mapping,
    trace_rows,
    signature_verifier: Callable,
    bench_rows,
    eval_report: Mapping,
    baseline_report: Mapping,
    witness_digest: str,
    approved_bindings,
) -> dict:
    """Return a deterministic, non-authoritative PASS/HOLD breakdown.

    This is *not* the producer or verifier for signed routing authority.
    A positive report says only that the frozen synthetic test interfaces
    accepted their inputs. The caller cannot turn it into an authorization.
    """
    evidence = {}
    evidence["runtime"] = (
        "PASS_RESEARCH_ONLY"
        if type(stage_sample) is Sample and stage_sample.decision == "PASS_SAMPLE_ONLY"
        and stage_sample.attempts == 1 and stage_sample.latency_ms is not None
        else "HOLD"
    )

    receipt = verify_delegation(model_id, child_sessions, parent_receipt, child_receipt)
    evidence["agent_cost_and_identity"] = (
        "PASS_RESEARCH_ONLY" if receipt.decision == "PASS_RESEARCH_ONLY" else "HOLD"
    )

    try:
        context = build_verified_context(trace_rows, verifier=signature_verifier)
        evidence["sensorgraft"] = "PASS_RESEARCH_ONLY" if context.selected else "HOLD"
    except (TypeError, ValueError):
        evidence["sensorgraft"] = "HOLD"

    try:
        benchmark = assess_repeatability(bench_rows)
        evidence["fleet_benchmark"] = (
            "PASS_RESEARCH_ONLY"
            if isinstance(benchmark, dict) and benchmark.get("decision") == "PASS_RESEARCH_ONLY"
            else "HOLD"
        )
    except (TypeError, ValueError):
        evidence["fleet_benchmark"] = "HOLD"

    try:
        evaluation = evaluate_bound_witnessed(
            eval_report, baseline_report, witness_digest=witness_digest,
            approved_bindings=approved_bindings,
        )
        evidence["evaluation"] = (
            "PASS_RESEARCH_ONLY" if evaluation.decision == "PASS_TO_REVIEW" else "HOLD"
        )
    except (TypeError, ValueError):
        evidence["evaluation"] = "HOLD"

    return {
        "decision": "RESEARCH_CANDIDATE" if all(
            v == "PASS_RESEARCH_ONLY" for v in evidence.values()
        ) else "HOLD",
        "gates": evidence,
        "creates_provider_eligibility": False,
        "signed_admission_required": True,
        "production_authorized": False,
        "live_requests_sent": 0,
    }
