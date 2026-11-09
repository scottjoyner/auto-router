"""Offline, read-only lint for fictional LFM3 structured-decoding research.

The result is an API request *shape check*, NOT tested llama-server support,
model accuracy, independent case provenance or permission to make requests.
This module never issues model generation, I/O or network requests.
"""
from __future__ import annotations

import hashlib
import re

from .prospective_holdout_gate import canonical, sha
from .prospective_rule_v1 import CODES
from .source_fact_alignment_guard import _parse_model_answer, MARK

SCHEMA = "lfm3.dev.structured-decoding-static-preflight.v1"
SOURCE_CLASS = "fictional_synthetic_development"
LOCAL_MODEL_ALIAS = "lfm3-deathstar-unstructured-research"
MAX_TOKENS = 96
# Constrained decoding has only a *static* proposal until actual supported
# host execution with pinned llama-server source is independently observed.
FORMAT_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": sorted(CODES)},
        "evidence": {
            "anyOf": [
                {"type": "string", "enum": [f"E{i}" for i in range(1, 10)]},
                {"type": "null"},
            ]
        },
    },
    "required": ["category", "evidence"],
    "additionalProperties": False,
}
RESPONSE_FORMAT = {"type": "json_schema", "schema": FORMAT_SCHEMA}
EXPECTED_REQUEST_KEYS = frozenset({
    "model", "messages", "temperature", "max_tokens", "stream",
    "response_format", "chat_template_kwargs",
})


def hold(reason: str) -> dict:
    return {
        "decision": "HOLD",
        "reason": reason,
        "static_schema_shape_verified": False,
        "live_llama_server_support_verified": False,
        "model_generated_text_verified": False,
        "actual_model_execution_verified": False,
        "independent_source_custody_verified": False,
        "model_value_verified": False,
        "permitted_to_infer": False,
        "production_authorized": False,
    }


def inspect_request_shape(request: object, *, declared_data_class: object) -> dict:
    """Refuse wrong JSON-schema location and any generation-policy drift.

    The request may contain private prompts. Return only hash/boolean
    metadata and NEVER echo source text to logs or stdout.
    """
    try:
        if type(declared_data_class) is not str or declared_data_class != SOURCE_CLASS:
            return hold("unapproved_development_data_class")
        if type(request) is not dict or set(request) != EXPECTED_REQUEST_KEYS:
            return hold("unallowlisted_request_fields")
        if (request["model"] != LOCAL_MODEL_ALIAS or
            type(request["temperature"]) not in (int, float) or
            request["temperature"] != 0 or
            type(request["max_tokens"]) is not int or request["max_tokens"] != MAX_TOKENS or
            request["stream"] is not False or
            type(request["chat_template_kwargs"]) is not dict or
            set(request["chat_template_kwargs"]) != {"enable_thinking"} or
            request["chat_template_kwargs"]["enable_thinking"] is not False):
            return hold("model_or_generation_settings_drift")
        prompts=request["messages"]
        if (type(prompts) is not list or len(prompts)!=1 or
            type(prompts[0]) is not dict or set(prompts[0])!={"role","content"} or
            prompts[0]["role"]!="user" or type(prompts[0]["content"]) is not str):
            return hold("unexpected_message_shape")
        prompt=prompts[0]["content"]
        if (not 80 <= len(prompt) <= 7800 or
            not prompt.startswith("OFFLINE FICTIONAL DEVELOPMENT TRIAGE ONLY.")):
            return hold("not_an_allowlisted_fictional_development_prompt")
        # llama.cpp's documented /v1/chat/completions response_format uses
        # a DIRECT "schema" key. Its OpenAI-inspired nested alternatives are
        # intentionally excluded until independently verified on this build.
        fmt=request["response_format"]
        if (type(fmt) is not dict or
            set(fmt) != {"type", "schema"} or
            type(fmt["schema"]) is not dict or
            # Canonical JSON preserves the distinction between false and 0,
            # unlike Python dict equality (False == 0).
            sha(fmt) != sha(RESPONSE_FORMAT)):
            return hold("not_pinned_llamacpp_direct_schema_format")
        return {
            "decision": "PASS_STATIC_DEVELOPMENT_SCHEMA_REQUEST_ONLY",
            "reason": "static documented response_format shape only; server not queried",
            "response_format_sha256": sha(RESPONSE_FORMAT),
            "source_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "static_schema_shape_verified": True,
            "live_llama_server_support_verified": False,
            "model_generated_text_verified": False,
            "actual_model_execution_verified": False,
            "independent_source_custody_verified": False,
            "model_value_verified": False,
            "permitted_to_infer": False,
            "production_authorized": False,
        }
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError):
        return hold("malformed_or_untrusted_static_request")


def inspect_existing_reply(note: object, reply: object,
                           *, declared_data_class: object) -> dict:
    """Strict field and source marker check; never infers semantic truth."""
    try:
        if type(declared_data_class) is not str or declared_data_class != SOURCE_CLASS:
            return hold("unapproved_development_data_class")
        if type(note) is not str or not 30 <= len(note) <= 4000:
            return hold("invalid_fictional_source_note")
        markers=MARK.findall(note)
        if not markers or len(markers)!=len(set(markers)) or len(markers)>9:
            return hold("invalid_or_duplicated_note_evidence_markers")
        answer=_parse_model_answer(reply)
        if answer is None:
            return hold("response_does_not_satisfy_strict_two_key_schema")
        if answer["evidence"] is not None and answer["evidence"] not in markers:
            return hold("evidence_marker_missing_in_source_note")
        return {
            "decision": "PASS_STATIC_REPLY_SYNTAX_ONLY",
            "reason": "syntactically valid category and source marker; semantic accuracy not verified",
            "response_sha256": hashlib.sha256(reply.encode()).hexdigest(),
            "static_schema_shape_verified": True,
            "live_llama_server_support_verified": False,
            "model_generated_text_verified": False,
            "actual_model_execution_verified": False,
            "independent_source_custody_verified": False,
            "model_value_verified": False,
            "permitted_to_infer": False,
            "production_authorized": False,
        }
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError):
        return hold("malformed_or_untrusted_static_reply")
