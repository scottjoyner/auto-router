"""Pure, offline, gold-free case projection for a FUTURE research custodian.

Neither caller-supplied signatures nor a label-free projection authenticate
independent authors, external timestamp chronology or permission to infer.
Never runs a model, reads credentials or writes/dispatches a packet.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from .independent_custody_handoff import verify_handoff
from .prospective_holdout_gate import CODES, MODEL_SHA, canonical, sha

SCHEMA = "lfm3.prospective.blind-case-packet.v1"
RECEIPT_SCHEMA = "lfm3.prospective.blind-projection-receipt.v1"
DATA_CLASS = "fictional_synthetic_research"
# Conservative catches, not a complete semantic inspection for hidden labels.
GOLD_HINT = re.compile(r"(?i)\b(?:expected|gold\s*answer|gold\s*label|ground\s*truth|correct\s*category|oracle)\s*[:=]")


def hold(reason: str) -> dict:
    return {
        "decision": "HOLD", "reason": reason,
        "projection_structure_verified": False,
        "independent_author_verified": False,
        "trusted_key_ownership_verified": False,
        "external_case_custody_verified": False,
        "pre_inference_seal_verified": False,
        "permitted_to_infer": False,
        "model_value_verified": False,
        "production_authorized": False,
    }


def prepare_blind_packet(dataset: object, handoff: object,
                         trusted_key_roster: object,
                         *, declared_data_class: object,
                         blinding_secret: object) -> dict:
    """Validate signatures then project ONLY ids and source notes.

    This is an *operator preview packet*. Authenticated external custody
    must be checked separately before handing notes to an inference operator.
    """
    try:
        if type(declared_data_class) is not str or declared_data_class != DATA_CLASS:
            return hold("unapproved_or_unproven_data_class")
        # A random, custodian-only 256-bit secret prevents the source case
        # identifiers from leaking category/order hints to the model operator.
        # Actual CLI generates it with secrets.token_bytes(32), never exports it.
        if type(blinding_secret) is not bytes or len(blinding_secret)!=32:
            return hold("missing_or_weak_opaque_identifier_secret")
        signed=verify_handoff(dataset,handoff,trusted_key_roster)
        if signed["decision"] != "PASS_OFFLINE_SIGNATURE_STRUCTURE_ONLY":
            return hold("signature_" + signed["reason"])
        # Anti-leak heuristic: developer-created synthetic fixtures intentionally
        # contain 'category=<gold label>' and MUST be rejected, not laundered.
        for case in dataset["cases"]:
            note=case["note"]
            if GOLD_HINT.search(note) or any(
                re.search(r"(?i)(?<!\w)" + re.escape(code) + r"(?!\w)",note)
                for code in CODES
            ):
                return hold("suspected_gold_label_leak_in_source_note")
        alias_map={}
        for entry in dataset["cases"]:
            alias="B" + hmac.new(blinding_secret, entry["id"].encode("ascii"),
                                   hashlib.sha256).hexdigest()[:24].upper()
            if alias in alias_map:
                return hold("opaque_case_identifier_collision")
            alias_map[alias]=entry["id"]
        by_id={entry["id"]:entry["note"] for entry in dataset["cases"]}
        prompt_cases=[{"id":alias,"note":by_id[original]}
                      for alias,original in sorted(alias_map.items())]
        packet={
            "schema": SCHEMA,
            # Dataset hash commits GOLD labels too; retain it ONLY in the
            # custodian receipt to reduce known-label guessing by operators.
            "checkpoint_sha256": MODEL_SHA,
            "case_count": len(prompt_cases),
            "data_class_self_declared": DATA_CLASS,
            "cases": prompt_cases,
            "external_custody_verified": False,
            "permitted_to_infer": False,
            "production_authorized": False,
        }
        receipt={
            "schema": RECEIPT_SCHEMA,
            "source_dataset_sha256": signed["dataset_sha256"],
            "blind_prompt_view_sha256": sha(packet),
            "case_count": len(prompt_cases),
            # Private original-ID mapping is never present in the prompt packet.
            "opaque_id_to_original_id": dict(sorted(alias_map.items())),
            "offline_three_role_signature_structure_verified": True,
            "gold_keys_omitted": True,
            "independent_author_verified": False,
            "trusted_key_ownership_verified": False,
            "external_case_custody_verified": False,
            "pre_inference_seal_verified": False,
            "permitted_to_infer": False,
            "model_value_verified": False,
            "production_authorized": False,
        }
        return {
            "decision": "PASS_OFFLINE_GOLD_FREE_PROJECTION_ONLY",
            "reason": "syntactic note-only packet; out-of-band independent custody and release still required",
            "packet": packet,
            "receipt": receipt,
            "projection_structure_verified": True,
            "independent_author_verified": False,
            "trusted_key_ownership_verified": False,
            "external_case_custody_verified": False,
            "pre_inference_seal_verified": False,
            "permitted_to_infer": False,
            "model_value_verified": False,
            "production_authorized": False,
        }
    except (TypeError, KeyError, ValueError, OSError, OverflowError,
            RecursionError, UnicodeError):
        return hold("invalid_or_unavailable_blind_projection_input")
