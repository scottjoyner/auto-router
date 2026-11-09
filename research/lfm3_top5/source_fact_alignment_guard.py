"""Research-only model-label consistency check against separately authored typed facts.

The fact table is a caller-supplied *synthetic* annotation, not external
verification. Never treat model text as evidence or grant release authority.
No model inference, Git operations, network calls, credentials or live reads.
"""
from __future__ import annotations
import hashlib
import json
import re
from .prospective_rule_v1 import CODES
from .unstructured_triage_audit import _pairs_no_duplicate, _invalid_constant

SCHEMA = "lfm3.typed-source-facts.synthetic.v1"
ORIGIN = "synthetic_operator_annotation"
FACTS = frozenset({
    "tls_unrecognized_name", "sni_mapping_reviewed", "sni_host_binding_present",
    "machine_task_http_401", "machine_identity_contract_mismatch",
    "model_generation_completed", "signed_tensor_witness_available",
    "beta_group_reviewed", "beta_group_build_linked",
    "database_probe_completed", "postgres_listener_present",
})
MARK = re.compile(r"(?<![A-Za-z0-9_])(E[1-9])(?:\s*\([^)]{0,90}\))?\s*:")
RULES = {
    "tls_sni_missing": {"sni_mapping_reviewed": True,
                        "sni_host_binding_present": False},
    "node_auth_contract": {"machine_task_http_401": True,
                           "machine_identity_contract_mismatch": True},
    "gpu_attestation_missing": {"model_generation_completed": True,
                                "signed_tensor_witness_available": False},
    "beta_build_unlinked": {"beta_group_reviewed": True,
                            "beta_group_build_linked": False},
    "database_service_down": {"database_probe_completed": True,
                              "postgres_listener_present": False},
}

def hold(reason: str) -> dict:
    return {"decision": "HOLD", "reason": reason, "model_category_supported": False,
            "independent_source_custody_verified": False,
            "model_value_verified": False, "production_authorized": False}

def _parse_model_answer(raw: object):
    if type(raw) is not str or not 1 <= len(raw) <= 2000 or "<think>" in raw.lower():
        return None
    try:
        obj = json.loads(raw, object_pairs_hook=_pairs_no_duplicate,
                         parse_constant=_invalid_constant)
    except (ValueError, TypeError, RecursionError):
        return None
    if type(obj) is not dict or set(obj) != {"category", "evidence"}:
        return None
    if type(obj["category"]) is not str or obj["category"] not in CODES:
        return None
    ev = obj["evidence"]
    if ev is not None and (type(ev) is not str or
                           not re.fullmatch(r"E[1-9]", ev)):
        return None
    if ev is None and obj["category"] != "insufficient_evidence":
        return None
    return obj

def _validated_facts(note: object, record: object):
    if type(note) is not str or not 30 <= len(note) <= 4000:
        return None, "invalid_source_note"
    found = MARK.findall(note)
    if not found or len(found) != len(set(found)) or len(found) > 9:
        return None, "missing_or_duplicate_source_markers"
    if type(record) is not dict or set(record) != {
        "schema", "origin", "note_sha256", "evidence",
        "independent_custody", "production_authorized"
    }:
        return None, "invalid_fact_table_shape"
    if (record["schema"] != SCHEMA or record["origin"] != ORIGIN or
        record["independent_custody"] is not False or
        record["production_authorized"] is not False):
        return None, "unauthorized_fact_table_claim"
    if (type(record["note_sha256"]) is not str or
        record["note_sha256"] != hashlib.sha256(note.encode()).hexdigest()):
        return None, "source_note_digest_mismatch"
    entries = record["evidence"]
    if type(entries) is not list or len(entries) != len(found):
        return None, "incomplete_evidence_marker_review"
    results,seen = [],set()
    for item in entries:
        if type(item) is not dict or set(item) != {
            "marker", "review_status", "facts"
        }:
            return None, "malformed_evidence_entry"
        marker=item["marker"]
        if type(marker) is not str or marker not in found or marker in seen:
            return None, "unknown_or_repeated_evidence_marker"
        seen.add(marker)
        if item["review_status"] not in ("verified", "unverified", "not_observed"):
            return None, "bad_review_status"
        facts=item["facts"]
        if type(facts) is not dict or not set(facts).issubset(FACTS):
            return None, "unallowlisted_fact_key"
        if any(type(v) is not bool for v in facts.values()):
            return None, "nonboolean_source_fact"
        if item["review_status"] != "verified" and facts:
            return None, "unverified_fact_injected"
        results.append({"marker":marker,"review_status":item["review_status"],"facts":facts})
    if seen != set(found):
        return None, "unreviewed_evidence_markers"
    return results,None

def assess_source_label(note: object, source_facts: object, model_answer: object) -> dict:
    """Pure consistency guard: PASS is advisory only, never source attestation."""
    try:
        entries,error=_validated_facts(note,source_facts)
        if error is not None:
            return hold(error)
        answer=_parse_model_answer(model_answer)
        if answer is None:
            return hold("model_json_not_exact_two_key_contract")
        supported=[]
        for entry in entries:
            if entry["review_status"] != "verified":
                continue
            facts=entry["facts"]
            for code,requirements in RULES.items():
                if all(facts.get(k) is v for k,v in requirements.items()):
                    supported.append((code,entry["marker"]))
        supported=set(supported)
        categories={c for c,_ in supported}
        if len(categories)>1:
            return hold("multiple_source_supported_categories")
        if not supported:
            if answer["category"] != "insufficient_evidence":
                return hold("positive_model_category_lacks_source_facts")
            if answer["evidence"] is None and any(
                e["review_status"] == "verified" for e in entries
            ):
                return hold("missing_supporting_abstention_marker")
            if answer["evidence"] is not None and not any(
                e["marker"]==answer["evidence"] and
                e["review_status"]=="verified" for e in entries
            ):
                return hold("abstention_marker_not_reviewed_as_verified")
            return {"decision":"PASS_SYNTHETIC_ABSTENTION_ALIGNMENT_ONLY",
                    "reason":"no complete positive source signal in synthetic annotations",
                    "model_category_supported": True,
                    "independent_source_custody_verified": False,
                    "model_value_verified": False, "production_authorized": False}
        if answer["category"] not in categories:
            return hold("model_category_contradicts_typed_source_facts")
        if (answer["category"],answer["evidence"]) not in supported:
            return hold("model_evidence_marker_does_not_support_category")
        return {"decision":"PASS_SYNTHETIC_FACT_ALIGNMENT_ONLY",
                "reason":"model label and marker align with synthetic typed observations",
                "model_category_supported": True,
                "independent_source_custody_verified": False,
                "model_value_verified": False, "production_authorized": False}
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        return hold("malformed_untrusted_input")
