"""Deterministically reconcile four full-cohort research stages with the frozen scorer.

This pure, offline integration NEVER verifies external custody, independently
witnessed execution, generalization or production eligibility. A valid fake
three-role signature bundle is not a trusted external seal.
"""
from __future__ import annotations

import argparse
import json

from .unstructured_triage_audit import _pairs_no_duplicate, _invalid_constant

from .independent_custody_handoff import verify_handoff
from .prospective_holdout_gate import (
    MIN_ABSOLUTE_GAIN, EXACT_FLOOR, MODES, canonical, evaluate, sha
)
from .session_stability_gate import STAGE_ORDER, audit_repeatability

SCHEMA = "lfm3.prospective.primary-reconciliation.v1"
SELECTOR = "lexicographic-case-id-alternating-first-second-session-v1"

def hold(reason: str) -> dict:
    return {
        "decision": "HOLD",
        "reason": reason,
        "primary_case_selection_verified": False,
        "offline_signature_structure_verified": False,
        "repeatability_structure_verified": False,
        "scoring_contract_verified": False,
        "independent_author_verified": False,
        "trusted_key_ownership_verified": False,
        "external_anchor_verified": False,
        "pre_inference_seal_verified": False,
        "external_custody_verified": False,
        "model_value_verified": False,
        "accelerator_attested": False,
        "production_authorized": False,
    }

def _rows_from_fixed_stage_parity(dataset: dict, capture: dict) -> dict:
    """No model prediction, accuracy or label informs the selection.

    Each sorted case ID is assigned alternating FIRST/SECOND stage before
    inspecting any model output. Both modes receive the identical case split.
    Session IDs must include two distinct stage starts per mode (scorer gate).
    """
    ids = sorted(r["id"] for r in dataset["cases"])
    if len(ids) < 2:
        raise ValueError("insufficient case count")
    mode_stages = {mode: [] for mode in MODES}
    for index, stage in enumerate(capture["stages"]):
        if stage["mode"] != STAGE_ORDER[index]:
            raise ValueError("wrong stage order")
        mode_stages[stage["mode"]].append(stage)
    result = {}
    for mode in MODES:
        if len(mode_stages[mode]) != 2:
            raise ValueError("missing session for mode")
        stages = mode_stages[mode]
        by_stage = []
        for stage in stages:
            m = {r["case_id"]: r for r in stage["requests"]}
            if len(m) != len(ids) or set(m) != set(ids):
                raise ValueError("stage cohort mismatch")
            by_stage.append(m)
        selected = []
        for rank, cid in enumerate(ids):
            selected_stage = stages[rank % 2]
            row = by_stage[rank % 2][cid]
            selected.append({
                "id": cid,
                "response": row["response"],
                "request_id": row["request_id"],
                "session_id": selected_stage["session_id"],
                "http_plus_validation_ms": row["http_plus_validation_ms"],
            })
        if len({r["session_id"] for r in selected}) != 2:
            raise ValueError("not two independent stage identities")
        result[mode] = selected
    return result

def reconcile_primary(manifest: object, dataset: object, bundle: object,
                      trusted_key_roster: object, capture: object) -> dict:
    """Validate signatures, all four stages and the unchanged scorer in order.

    A structural PASS has no independent trust. The caller must separately
    authenticate signers, establish prior external seal and approve inference.
    The function only reconciles already-supplied offline responses.
    """
    try:
        signed = verify_handoff(dataset, bundle, trusted_key_roster)
        if signed["decision"] != "PASS_OFFLINE_SIGNATURE_STRUCTURE_ONLY":
            return hold("handoff_" + signed["reason"])
        dataset_digest = signed["dataset_sha256"]
        repeat = audit_repeatability(dataset, dataset_digest, capture)
        if repeat["decision"] != "PASS_OFFLINE_PAIRING_CONTRACT_ONLY":
            return hold("repeatability_" + repeat["reason"])
        rows = _rows_from_fixed_stage_parity(dataset, capture)
        score = evaluate(manifest, dataset, dataset_digest, rows)
        if score["decision"] != "PASS_OFFLINE_SCORING_CONTRACT_ONLY":
            return hold("scorer_" + score["reason"])
        count = len(dataset["cases"])
        if (repeat["unique_case_count"] != count or
            repeat["raw_request_count"] != 4*count or
            score["primary_independent_case_count"] != count or
            any(score["model_modes"][mode]["unique_case_count"] != count
                for mode in MODES)):
            return hold("case_count_or_response_cardinality_mismatch")
        quality = {}
        for mode in MODES:
            measured = score["model_modes"][mode]
            paired = repeat["modes"][mode]
            baseline = measured["baseline_exact_correct"]
            repeated_floor = all(
                value/count >= EXACT_FLOOR and
                (value-baseline)/count >= MIN_ABSOLUTE_GAIN
                for value in (paired["first_correct"],paired["second_correct"])
            )
            quality[mode] = {
                "primary_meets_frozen_quality_floor": bool(
                    measured["meets_preregistered_quality_floor"]),
                "both_repeat_stages_meet_rate_and_gain_floor": bool(repeated_floor),
                "repeat_correctness_flip_cases": paired["correctness_flip_cases"],
                "repeat_unsafe_authority_claim_attempts":
                    paired["unsafe_authority_claim_attempts"],
                "advisory_no_regression_signal": bool(
                    repeated_floor and measured["meets_preregistered_quality_floor"]
                    and paired["correctness_flip_cases"] == 0
                    and paired["unsafe_authority_claim_attempts"] == 0),
                "independent_quality_verified": False,
            }
        # One case per mode contributes to primary scoring, but the complete
        # four-stage record remains available for the independent reviewer.
        selection_material = {
            "schema": SCHEMA,
            "selector": SELECTOR,
            "dataset_sha256": dataset_digest,
            "mode_case_request_ids": {
                mode: [{"id": row["id"], "request_id": row["request_id"],
                        "session_id": row["session_id"]}
                       for row in rows[mode]]
                for mode in MODES
            }
        }
        return {
            "decision": "PASS_OFFLINE_RECONCILIATION_CONTRACT_ONLY",
            "selector": SELECTOR,
            "unique_case_count": count,
            "primary_answer_rows": 2*count,
            "all_stage_answer_rows": 4*count,
            "dataset_sha256": dataset_digest,
            "capture_sha256": sha(capture),
            "selection_manifest_sha256": sha(selection_material),
            "mode_scores": score["model_modes"],
            "repeatability": repeat["modes"],
            "quality_development_screen": quality,
            "primary_case_selection_verified": True,
            "offline_signature_structure_verified": True,
            "repeatability_structure_verified": True,
            "scoring_contract_verified": True,
            "independent_author_verified": False,
            "trusted_key_ownership_verified": False,
            "external_anchor_verified": False,
            "pre_inference_seal_verified": False,
            "external_custody_verified": False,
            "model_value_verified": False,
            "accelerator_attested": False,
            "production_authorized": False,
            "reason": "Synthetic offline contract integration only; no trusted signer, external seal, independent trial, kernel attestation or release authority.",
        }
    except (TypeError, KeyError, ValueError, RecursionError,
            OverflowError, OSError, ZeroDivisionError):
        return hold("untrusted_or_incomplete_reconciliation_input")

def _load_json(path: str) -> object:
    with open(path, encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=_pairs_no_duplicate,
                         parse_constant=_invalid_constant)

def main() -> int:
    """Run offline only; output no case notes, labels, or raw model replies."""
    parser=argparse.ArgumentParser(
        description="Validate four-stage signed LFM3 research evidence (never production approval)")
    for opt in ("manifest","dataset","bundle","key-roster","capture"):
        parser.add_argument("--"+opt,required=True)
    args=parser.parse_args()
    try:
        result=reconcile_primary(
            _load_json(args.manifest),_load_json(args.dataset),
            _load_json(args.bundle),_load_json(args.key_roster),
            _load_json(args.capture))
    except (ValueError, TypeError, OSError, RecursionError, OverflowError):
        result=hold("invalid_or_missing_local_json_inputs")
    print(canonical(result))
    return 0 if result["decision"]=="PASS_OFFLINE_RECONCILIATION_CONTRACT_ONLY" else 2

if __name__=="__main__":
    raise SystemExit(main())
