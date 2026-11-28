"""Safety and evidence handling of the strictly local MiniCPM equivalence canary."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/joyner_minicpm_guarded_canary.py"
SPEC = importlib.util.spec_from_file_location("joyner_guarded_canary", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def receipt():
    return {
        "verified_guarded_model_sha256_matches": True,
        "anonymous_guarded_model_list_status": 401,
        "candidate_model_aliases_match": True,
        "legacy": {"http_status": 200, "answer_42_present": True},
        "guarded": {"http_status": 200, "answer_42_present": True},
    }


def test_synthetic_success_never_authorizes_cutover():
    outcome = MODULE.assess_receipt(receipt())
    assert outcome["functional_canary_gates_passed"]
    assert outcome["can_cutover"] is False
    assert outcome["can_stop_service"] is False
    assert "per_request_trace_custody" in outcome["remaining_gaps"]


def test_legacy_and_guarded_fail_quality_independently():
    for field in ("legacy", "guarded"):
        r = receipt()
        r[field]["answer_42_present"] = False
        outcome = MODULE.assess_receipt(r)
        assert not outcome["functional_canary_gates_passed"]
        assert outcome["can_cutover"] is False


def test_alias_mismatch_denied_despite_same_model_weights():
    r = receipt()
    r["candidate_model_aliases_match"] = False
    outcome = MODULE.assess_receipt(r)
    assert "model_alias_mismatch" in outcome["reasons"]


def test_missing_identity_denied():
    r = receipt()
    r["verified_guarded_model_sha256_matches"] = False
    outcome = MODULE.assess_receipt(r)
    assert "guarded_weights_not_verified" in outcome["reasons"]


def test_no_redirect_or_arbitrary_endpoint_is_allowed():
    with pytest.raises(ValueError, match="unexpected endpoint"):
        MODULE.http_json("https://other.example.invalid/v1/models", key="fake")


def test_anonymous_status_is_independent_from_authorized_result():
    r = receipt()
    r["anonymous_guarded_model_list_status"] = 200
    outcome = MODULE.assess_receipt(r)
    assert "anonymous_guarded_access_not_rejected" in outcome["reasons"]


def test_receipt_does_not_contain_model_text_or_bearer_key():
    raw = MODULE.infer_result(200, {"content": "42", "timings": {"predicted_n": 1}}, 4)
    assert raw["answer_42_present"]
    assert raw["generated_tokens"] == 1
    text = json.dumps(raw)
    assert "42" not in text.replace('"answer_42_present": true', "")
    assert "content" in text
    assert "Authorization" not in text
    assert "Bearer" not in text
