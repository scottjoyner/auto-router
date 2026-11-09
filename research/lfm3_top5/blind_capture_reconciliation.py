"""Custodian-only, offline blind-alias reversal and frozen research scoring.

A caller-supplied signed corpus and blind packet are not independently
trusted custody, prior sealing, real model execution, or release approval.
Never exports gold or original case IDs, runs inference or changes providers.
"""
from __future__ import annotations

import argparse
import json
import re
from .blind_case_intake import SCHEMA as PACKET_SCHEMA, RECEIPT_SCHEMA, DATA_CLASS, GOLD_HINT
from .prospective_rule_v1 import CODES
from .independent_custody_handoff import verify_handoff
from .primary_capture_reconciliation import reconcile_primary
from .prospective_holdout_gate import MODEL_SHA, canonical, sha
from .session_stability_gate import SCHEMA as STABILITY_SCHEMA
from .unstructured_triage_audit import _invalid_constant, _pairs_no_duplicate

SCHEMA = "lfm3.prospective.blind-four-stage-capture.v1"
PACKET_KEYS = frozenset({
    "schema", "checkpoint_sha256", "case_count", "data_class_self_declared",
    "cases", "external_custody_verified", "permitted_to_infer", "production_authorized"
})
RECEIPT_KEYS = frozenset({
    "schema", "source_dataset_sha256", "blind_prompt_view_sha256", "case_count",
    "opaque_id_to_original_id", "offline_three_role_signature_structure_verified",
    "gold_keys_omitted", "independent_author_verified", "trusted_key_ownership_verified",
    "external_case_custody_verified", "pre_inference_seal_verified", "permitted_to_infer",
    "model_value_verified", "production_authorized"
})
ALIASES = re.compile(r"B[0-9A-F]{24}")


def hold(reason: str) -> dict:
    return {
        "decision": "HOLD", "reason": reason,
        "blind_alias_mapping_verified": False,
        "four_stage_capture_verified": False,
        "frozen_scoring_verified": False,
        "independent_author_verified": False,
        "external_case_custody_verified": False,
        "pre_inference_seal_verified": False,
        "actual_model_execution_verified": False,
        "model_value_verified": False,
        "permitted_to_infer": False,
        "production_authorized": False,
    }


def reconcile_blind_capture(manifest: object, dataset: object, handoff: object,
                            roster: object, packet: object, receipt: object,
                            blind_capture: object) -> dict:
    """Restore private original case IDs in memory only, score, emit counts.

    Require every caller-supplied record in this one local process. Nothing
    written to disk or returned includes the original IDs, gold, or notes.
    """
    try:
        signed=verify_handoff(dataset,handoff,roster)
        if signed["decision"]!="PASS_OFFLINE_SIGNATURE_STRUCTURE_ONLY":
            return hold("signed_handoff_" + signed["reason"])
        count=len(dataset["cases"])
        if (type(packet) is not dict or set(packet)!=PACKET_KEYS or
            packet["schema"]!=PACKET_SCHEMA or
            packet["checkpoint_sha256"]!=MODEL_SHA or
            type(packet["case_count"]) is not int or packet["case_count"]!=count or
            packet["data_class_self_declared"]!=DATA_CLASS or
            any(packet[k] is not False for k in (
                "external_custody_verified","permitted_to_infer","production_authorized"))):
            return hold("malformed_or_unauthorized_blind_packet")
        if (type(receipt) is not dict or set(receipt)!=RECEIPT_KEYS or
            receipt["schema"]!=RECEIPT_SCHEMA or
            receipt["source_dataset_sha256"]!=signed["dataset_sha256"] or
            receipt["blind_prompt_view_sha256"]!=sha(packet) or
            type(receipt["case_count"]) is not int or receipt["case_count"]!=count or
            receipt["offline_three_role_signature_structure_verified"] is not True or
            receipt["gold_keys_omitted"] is not True or
            any(receipt[k] is not False for k in (
                "independent_author_verified","trusted_key_ownership_verified",
                "external_case_custody_verified","pre_inference_seal_verified",
                "permitted_to_infer","model_value_verified","production_authorized"))):
            return hold("blind_receipt_hash_or_authority_mismatch")
        mapping=receipt["opaque_id_to_original_id"]
        if (type(mapping) is not dict or len(mapping)!=count or
            not all(type(k) is str and ALIASES.fullmatch(k) and type(v) is str
                    for k,v in mapping.items())):
            return hold("invalid_opaque_case_mapping")
        source={x["id"]:x["note"] for x in dataset["cases"]}
        if len(set(mapping.values()))!=count or set(mapping.values())!=set(source):
            return hold("unknown_repeated_or_missing_original_case_id")
        rows=packet["cases"]
        if (type(rows) is not list or len(rows)!=count or
            any(type(row) is not dict or set(row)!={"id","note"} for row in rows)):
            return hold("malformed_model_facing_rows")
        packet_ids=[row["id"] for row in rows]
        if (any(type(i) is not str for i in packet_ids) or
            packet_ids!=sorted(mapping) or len(set(packet_ids))!=count):
            return hold("unsorted_or_missing_blinded_case_id")
        if any(row["note"]!=source[mapping[row["id"]]] for row in rows):
            return hold("blind_note_differs_from_signed_source")
        # Defense in depth: a forged but internally consistent packet/receipt
        # still cannot bypass the intake's basic explicit-gold note filter.
        if any(GOLD_HINT.search(row["note"]) or any(
            re.search(r"(?i)(?<!\w)"+re.escape(code)+r"(?!\w)", row["note"])
            for code in CODES) for row in rows):
            return hold("source_note_contains_gold_label_hint")
        if (type(blind_capture) is not dict or set(blind_capture)!={
            "schema","blind_prompt_view_sha256","checkpoint_sha256",
            "stages","production_authorized","independent_gpu_attested"} or
            blind_capture["schema"]!=SCHEMA or
            blind_capture["blind_prompt_view_sha256"]!=sha(packet) or
            blind_capture["checkpoint_sha256"]!=MODEL_SHA or
            blind_capture["production_authorized"] is not False or
            blind_capture["independent_gpu_attested"] is not False):
            return hold("capture_opaque_prompt_identity_or_authority")
        stages=blind_capture["stages"]
        if type(stages) is not list or len(stages)!=4:
            return hold("four_blinded_stages_required")
        restored=[]
        for stage in stages:
            if type(stage) is not dict or type(stage.get("requests")) is not list:
                return hold("invalid_blinded_stage")
            rebuilt=[]
            for row in stage["requests"]:
                if type(row) is not dict or type(row.get("case_id")) is not str:
                    return hold("invalid_blinded_request")
                alias=row["case_id"]
                if alias not in mapping:
                    return hold("unknown_opaque_case_id")
                new=dict(row)
                new["case_id"]=mapping[alias]
                rebuilt.append(new)
            rebuilt_stage=dict(stage)
            rebuilt_stage["requests"]=rebuilt
            restored.append(rebuilt_stage)
        restored_capture={
            "schema":STABILITY_SCHEMA,"dataset_sha256":signed["dataset_sha256"],
            "checkpoint_sha256":MODEL_SHA,"stages":restored,
            "production_authorized":False,"independent_gpu_attested":False,
        }
        combined=reconcile_primary(manifest,dataset,handoff,roster,restored_capture)
        if combined["decision"]!="PASS_OFFLINE_RECONCILIATION_CONTRACT_ONLY":
            return hold("frozen_reconciliation_"+combined["reason"])
        if (combined["unique_case_count"]!=count or
            combined["all_stage_answer_rows"]!=4*count or
            combined["primary_answer_rows"]!=2*count):
            return hold("capture_count_or_cardinality_mismatch")
        # Redacted summary ONLY; do not return source IDs, notes, oracle,
        # original-to-blind mapping or any raw model response.
        return {
            "decision":"PASS_OFFLINE_BLIND_CAPTURE_RECONCILIATION_ONLY",
            "reason":"four-stage synthetic structural reconciliation only; no independent source or execution proof",
            "blind_prompt_view_sha256":sha(packet),
            "unique_case_count":count,
            "four_stage_observations":4*count,
            "primary_scored_observations":2*count,
            "mode_exact_counts":{mode:combined["mode_scores"][mode]["exact_correct"]
                                 for mode in ("cpu","vulkan8")},
            "blind_alias_mapping_verified":True,
            "four_stage_capture_verified":True,
            "frozen_scoring_verified":True,
            "independent_author_verified":False,
            "external_case_custody_verified":False,
            "pre_inference_seal_verified":False,
            "actual_model_execution_verified":False,
            "model_value_verified":False,
            "permitted_to_infer":False,
            "production_authorized":False,
        }
    except (TypeError,KeyError,ValueError,OverflowError,OSError,
            RecursionError,UnicodeError):
        return hold("malformed_or_unavailable_blind_capture_input")


def _load(path: str):
    with open(path,encoding="utf-8") as f:
        return json.load(f,object_pairs_hook=_pairs_no_duplicate,
                         parse_constant=_invalid_constant)


def main() -> int:
    p=argparse.ArgumentParser(description="Offline private blinded-case reconciliation; NEVER inference or release admission")
    for name in ("manifest","dataset","bundle","key-roster","packet","receipt","capture"):
        p.add_argument("--"+name,required=True)
    a=p.parse_args()
    try:
        out=reconcile_blind_capture(*(
            _load(getattr(a,name.replace('-','_'))) for name in
            ("manifest","dataset","bundle","key-roster","packet","receipt","capture")
        ))
    except (TypeError,KeyError,OSError,ValueError,UnicodeError,RecursionError):
        out=hold("unreadable_or_invalid_local_input")
    print(canonical(out))
    return 0 if out["decision"]=="PASS_OFFLINE_BLIND_CAPTURE_RECONCILIATION_ONLY" else 2

if __name__=="__main__":
    raise SystemExit(main())
