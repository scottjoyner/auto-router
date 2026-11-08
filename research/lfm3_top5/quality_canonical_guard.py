"""Deterministic, fail-closed guards for synthetic fleet model output.

No model-generated text is promoted to release authority. This module is
pure and deploy-inert, and caller-supplied snapshots are NOT signed
independent production evidence. It demonstrates source-grounded
normalization only, without inventing missing nodes or missing gates.
"""
from __future__ import annotations
import json,re

NAME=re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
STATES={"PASS","HOLD","FAIL"}

def _no_constant(value):
    raise ValueError("invalid_json_constant")

def _unique_pairs(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise ValueError("duplicate_json_key")
        out[key]=value
    return out

def _decode_object(text):
    if type(text) is not str or not 1<=len(text)<=2048 or "<think>" in text.lower():
        raise ValueError("untrusted_or_unbounded_text")
    value=json.loads(text,object_pairs_hook=_unique_pairs,parse_constant=_no_constant)
    if type(value) is not dict:raise ValueError("not_single_json_object")
    return value

def _parse_trusted_states(snapshot,required):
    # The word "trusted" means caller-controlled, not externally signed.
    # No verification of ownership/custody or release authorization here.
    if type(snapshot) is not dict or type(required) not in (list,tuple):
        raise ValueError("invalid_source_types")
    if not 1<=len(required)<=32 or not 1<=len(snapshot)<=32:
        raise ValueError("empty_or_oversized_source")
    if any(type(x) is not str or NAME.fullmatch(x) is None for x in required):
        raise ValueError("invalid_required_name")
    if len(set(required))!=len(required) or set(snapshot)!=set(required):
        raise ValueError("missing_or_extra_trusted_source")
    if any(type(value) is not str or value not in STATES for value in snapshot.values()):
        raise ValueError("unrecognized_trusted_status")
    return snapshot

def node_summary_guard(answer,*,source_snapshot,required_nodes):
    """Accept unordered output only if EVERY node and its class match source.

    Always returns production_authorized=False; caller source custody
    is not independently attested, and this is research, not a route.
    """
    base={"decision":"HOLD","reason":"source_or_model_output_invalid",
          "semantic_matches_source":False,"canonical_output":None,
          "source_independently_signed":False,"production_authorized":False}
    try:
        snapshot=_parse_trusted_states(source_snapshot,required_nodes)
        content=_decode_object(answer)
        if set(content)!={"healthy","hold_or_fail","total"}:
            raise ValueError("unexpected_keys")
        healthy=content["healthy"];blocked=content["hold_or_fail"]
        total=content["total"]
        if type(total) is not int or total!=len(required_nodes):
            raise ValueError("invalid_total")
        if type(healthy) is not list or type(blocked) is not list:
            raise ValueError("non_list_values")
        for group in (healthy,blocked):
            if any(type(x) is not str or NAME.fullmatch(x) is None for x in group):
                raise ValueError("invalid_node_name")
            if len(set(group))!=len(group):
                raise ValueError("repeated_node")
        if set(healthy) & set(blocked):
            raise ValueError("node_in_both_groups")
        if set(healthy)|set(blocked)!=set(required_nodes):
            raise ValueError("missing_or_extra_node")
        source_good={node for node,state in snapshot.items() if state=="PASS"}
        source_blocked=set(required_nodes)-source_good
        if set(healthy)!=source_good or set(blocked)!=source_blocked:
            raise ValueError("model_disagrees_with_source")
        return {
            "decision":"PASS_SEMANTIC_NORMALIZATION_RESEARCH_ONLY",
            "reason":"all_reported_memberships_equal_authoritative_input",
            "semantic_matches_source":True,
            "canonical_output":{
                "healthy":sorted(healthy),"hold_or_fail":sorted(blocked),
                "total":total,
            },
            "source_independently_signed":False,
            "production_authorized":False,
        }
    except (ValueError,TypeError,KeyError):
        return base

def release_gate_guard(answer,*,source_gates,required_gates):
    """Never trust the model as a release witness. Exact match only.

    Even if source says all PASS, this function is NOT a deployment
    control and always denies authorization. Unknown gate/missing
    gate is HOLD, no alias/heuristic fixups.
    """
    base={"decision":"HOLD","reason":"missing_or_invalid_gate_source",
          "model_exactly_matches_source":False,"canonical_output":None,
          "source_independently_signed":False,"production_authorized":False}
    try:
        source=_parse_trusted_states(source_gates,required_gates)
    except (ValueError,TypeError):
        return base
    failed=sorted(key for key,value in source.items() if value!="PASS")
    passed=sorted(key for key,value in source.items() if value=="PASS")
    canonical={
        "decision":"HOLD" if failed else "PASS",
        "failed_gates":failed,"passed_gates":passed,
    }
    try:
        obj=_decode_object(answer)
        match=(json.dumps(obj,sort_keys=True,separators=(",",":"))==
               json.dumps(canonical,sort_keys=True,separators=(",",":")))
    except (ValueError,TypeError):
        match=False
    return {
        "decision":canonical["decision"],
        "reason":"deterministic_source_gate_evaluation_model_is_advisory",
        "model_exactly_matches_source":match,
        "canonical_output":canonical,
        "source_independently_signed":False,
        "production_authorized":False,
    }
