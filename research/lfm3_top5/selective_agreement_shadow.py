"""Offline, fictional-only agreement gate: LFM3 and the FROZEN rule must match.

This NEVER runs a model, approves a diagnosis, verifies custody, or authorizes
routing/production. A positive match is merely eligible for human examination.
"""
from __future__ import annotations
import hashlib
from pathlib import Path

from .prospective_holdout_gate import MODEL_SHA
from .prospective_rule_v1 import _MARKER, classify
from .source_fact_alignment_guard import _parse_model_answer

SCHEMA = "lfm3.fictional-development.shadow-agreement.v1"
SOURCE_CLASS = "fictional_synthetic_development"
FROZEN_RULE_SHA = "29ecfbfdcd3c5606690718e0416f093e737609187f5e527ab4c0625089d3b18e"


def hold(reason: str, *, agreed: bool = False) -> dict:
    return {
        "decision": "HOLD",
        "reason": reason,
        "model_and_rule_agreed": agreed,
        "candidate_for_manual_review": False,
        "independent_source_custody_verified": False,
        "actual_model_execution_verified": False,
        "model_value_verified": False,
        "safe_to_act": False,
        "production_authorized": False,
    }


def assess_shadow_candidate(payload: object) -> dict:
    """Compare already-supplied text; never try another inference or fallback.

    Caller-declared checkpoint and fictional classification are *untrusted*
    assertions, not signed observations. No branch grants release authority.
    """
    try:
        if type(payload) is not dict or set(payload) != {
            "schema", "source_class", "note", "model_answer",
            "claimed_model_sha256", "independent_source_custody",
            "production_authorized"
        }:
            return hold("malformed_shadow_input")
        if (payload["schema"] != SCHEMA or
            payload["source_class"] != SOURCE_CLASS or
            payload["independent_source_custody"] is not False or
            payload["production_authorized"] is not False):
            return hold("untrusted_or_prohibited_source_or_authority")
        if (type(payload["claimed_model_sha256"]) is not str or
            payload["claimed_model_sha256"] != MODEL_SHA):
            return hold("checkpoint_identity_claim_mismatch")
        source = Path(__file__).resolve().parent / "prospective_rule_v1.py"
        if hashlib.sha256(source.read_bytes()).hexdigest() != FROZEN_RULE_SHA:
            return hold("frozen_rule_source_changed")
        note = payload["note"]
        if type(note) is not str or not 80 <= len(note) <= 4000:
            return hold("invalid_or_unbounded_fictional_note")
        markers = [match.group("id").upper() for match in _MARKER.finditer(note)]
        if not 1 <= len(markers) <= 9 or len(markers) != len(set(markers)):
            return hold("missing_or_repeated_evidence_markers")
        model = _parse_model_answer(payload["model_answer"])
        if model is None:
            return hold("model_reply_not_exact_two_key_json")
        rule = classify(note)
        if type(rule) is not dict or set(rule) != {"category", "evidence"}:
            return hold("invalid_frozen_rule_output")
        if (model["evidence"] is not None and
            model["evidence"] not in markers):
            return hold("model_evidence_marker_not_in_source")
        if (rule["evidence"] is not None and
            rule["evidence"] not in markers):
            return hold("rule_evidence_marker_not_in_source")
        if model != rule:
            return hold("model_rule_category_or_evidence_disagreement")
        if model["category"] == "insufficient_evidence":
            return hold("mutual_insufficient_evidence_abstention", agreed=True)
        return {
            "decision": "PASS_UNTRUSTED_RESEARCH_AGREEMENT_ONLY",
            "reason": "fictional positive model and pinned rule agree; manual review still required",
            "model_and_rule_agreed": True,
            "candidate_for_manual_review": True,
            "proposed_category": model["category"],
            "proposed_evidence_marker": model["evidence"],
            "frozen_rule_sha256": FROZEN_RULE_SHA,
            "independent_source_custody_verified": False,
            "actual_model_execution_verified": False,
            "model_value_verified": False,
            "safe_to_act": False,
            "production_authorized": False,
        }
    except (TypeError, ValueError, KeyError, OSError, OverflowError,
            RecursionError, UnicodeError):
        return hold("invalid_or_unavailable_shadow_input")
