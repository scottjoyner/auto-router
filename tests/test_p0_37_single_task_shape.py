"""P0: only an EXACT signed single-prompt shape can qualify for future canary.

This does not execute a native task and does not enroll a release switch.
All signing keys are disposable fixture keys.
"""
from __future__ import annotations

import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from test_p0_37_request_digest import _delegation, _payload, _post
from test_p0_37_verified_user_delegation import _KID, _app

from auto_router.p0_single_task_shape import (
    SingleTaskShapeRejected,
    qualify_strict_single_native_task_shape,
)
from auto_router.user_delegation import delegated_body_digest, verify_user_delegation


def _actor(body):
    signer = Ed25519PrivateKey.generate()
    return verify_user_delegation(
        _delegation(signer, body),
        public_key=signer.public_key(),
        key_id=_KID, model=body.get("model", "auto/code"),
        request_path="/v1/chat/completions",
        request_body=body,
    )


@pytest.mark.parametrize("stream", [None, False, True])
def test_exact_signed_single_message_shape_is_candidate_not_execution(stream):
    body = _payload()
    if stream is not None:
        body["stream"] = stream
    verdict = qualify_strict_single_native_task_shape(
        verified_actor=_actor(body), original_request_body=body,
    )
    assert verdict == {
        "schema": "p0-single-native-task-shape-v1",
        "shape": "single_plain_text_chat_candidate",
        "expected_native_task_count": 1,
        "actual_native_task_count_observed": None,
        "native_parent_child_lineage_proven": False,
        "native_execution_attested": False,
        "physical_inference_permitted": False,
        "operator_approved": False,
        "response_delivery": "unknown",
    }
    assert "prompt" not in str(verdict)
    assert "tenant" not in verdict and "subject" not in verdict


def test_ordinary_unicode_short_message_keeps_original_digest_equivalence():
    body = _payload()
    body["messages"] = [{"role": "user", "content": "🔒 short synthetic canary"}]
    assert qualify_strict_single_native_task_shape(
        verified_actor=_actor(body), original_request_body=body
    )["expected_native_task_count"] == 1


@pytest.mark.parametrize("mutation", [
    lambda p: {**p, "n": 2},
    lambda p: {**p, "n": 1},  # even explicit n=1 blocked: no alternate schema.
    lambda p: {**p, "best_of": 2},
    lambda p: {**p, "prompt": ["a", "b"]},
    lambda p: {**p, "inputs": ["a", "b"]},
    lambda p: {**p, "batch_size": 2},
    lambda p: {**p, "parallel_tool_calls": True},
    lambda p: {**p, "tools": [{"type": "function"}]},
    lambda p: {**p, "tool_choice": "auto"},
    lambda p: {**p, "response_format": {"type": "json_object"}},
    lambda p: {**p, "user": "alice"},
    lambda p: {**p, "metadata": {"subject": "alice"}},
    lambda p: {**p, "id_slot": 0},
    lambda p: {**p, "seed": 1},
    lambda p: {**p, "temperature": 0},
    lambda p: {**p, "messages": [
        {"role": "user", "content": "one"},
        {"role": "user", "content": "two"},
    ]},
    lambda p: {**p, "messages": [{"role": "system", "content": "system"}]},
    lambda p: {**p, "messages": [{"role": "assistant", "content": "assistant"}]},
    lambda p: {**p, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "multimodal"}]}]},
    lambda p: {**p, "messages": [{"role": "user", "content": "a", "name": "bob"}]},
    lambda p: {**p, "messages": [{"role": "user", "content": ""}]},
    lambda p: {**p, "messages": [{"role": "user", "content": "x" * 513}]},
    lambda p: {**p, "messages": [{"role": "user", "content": "bad\x00byte"}]},
    lambda p: {**p, "messages": [{"role": "user", "content": "control\x01"}]},
    lambda p: {**p, "messages": "not a chat array"},
    lambda p: {**p, "max_tokens": 0},
    lambda p: {**p, "max_tokens": 17},
    lambda p: {**p, "max_tokens": True},
    lambda p: {**p, "max_tokens": 3.1},
    lambda p: {**p, "stream": "true"},
    lambda p: {**p, "stream": 1},
])
def test_even_signed_multi_task_or_ambiguous_canary_shapes_fail_closed(mutation):
    body = mutation(_payload())
    # Some malformed nested JSON values are still legitimate signed
    # bytes. Proof of signature does not authorize all request shapes.
    actor = _actor(body)
    with pytest.raises(SingleTaskShapeRejected):
        qualify_strict_single_native_task_shape(
            verified_actor=actor, original_request_body=body,
        )


@pytest.mark.parametrize("field, replacement", [
    ("issuer", "caller-json-user"),
    ("root_request_id", "not-a-uuid"),
    ("attempt_id", "not-a-uuid"),
    ("request_path", "/v1/embeddings"),
    ("request_sha256", "00" * 32),
])
def test_untrusted_or_mutated_actor_rejected(field, replacement):
    body = _payload()
    actor = _actor(body)
    actor[field] = replacement
    with pytest.raises(SingleTaskShapeRejected):
        qualify_strict_single_native_task_shape(
            verified_actor=actor, original_request_body=body,
        )


def test_caller_cannot_reuse_signed_single_request_for_changed_prompt_or_limits():
    body = _payload()
    actor = _actor(body)
    for altered in [
        {**body, "max_tokens": 9},
        {**body, "messages": [{"role": "user", "content": "changed prompt"}]},
        {**body, "model": "auto/review"},
    ]:
        with pytest.raises(SingleTaskShapeRejected):
            qualify_strict_single_native_task_shape(
                verified_actor=actor, original_request_body=altered,
            )


def test_wrong_original_root_and_attempt_are_rejected():
    body = _payload()
    actor = _actor(body)
    with pytest.raises(SingleTaskShapeRejected):
        qualify_strict_single_native_task_shape(
            verified_actor={**actor, "attempt_id": actor["root_request_id"]},
            original_request_body=body,
        )


def test_safe_gate_uses_actual_signed_asgi_actor_without_issuing_inference(
    tmp_path, monkeypatch,
):
    key = Ed25519PrivateKey.generate()
    app, observed, _ledger = _app(
        tmp_path, monkeypatch, key, require_body_digest=True,
    )
    body = _payload()
    signed = _delegation(key, body)
    with TestClient(app) as client:
        response = _post(client, signed, body)
    assert response.status_code == 200
    assert len(observed) == 1
    actor = observed[0]["actor"]
    assert actor["request_sha256"] == delegated_body_digest(
        "/v1/chat/completions", body
    )
    verdict = qualify_strict_single_native_task_shape(
        verified_actor=actor, original_request_body=body
    )
    assert verdict["expected_native_task_count"] == 1
    assert verdict["actual_native_task_count_observed"] is None
    assert verdict["physical_inference_permitted"] is False
    assert response.json()["id"] == "fixture-completion"


def test_malformed_actors_and_json_do_not_log_sensitive_prompt():
    body = _payload()
    body["messages"][0]["content"] = "NEVER_LOG_PRIVATE_FIXTURE"
    actor = _actor(body)
    with pytest.raises(SingleTaskShapeRejected) as caught:
        qualify_strict_single_native_task_shape(
            verified_actor={**actor, "request_sha256": "0" * 64},
            original_request_body=body,
        )
    assert "NEVER_LOG_PRIVATE_FIXTURE" not in str(caught.value)


def test_oversized_unicode_single_prompt_rejected_before_unbounded_digest():
    body = _payload()
    body["messages"][0]["content"] = "👋" * 512
    actor = _actor(body)
    with pytest.raises(SingleTaskShapeRejected, match="exceeds bound"):
        qualify_strict_single_native_task_shape(
            verified_actor=actor, original_request_body=body,
        )


def test_same_root_signed_retry_still_only_potentially_one_native_task():
    body = _payload()
    signer = Ed25519PrivateKey.generate()
    original = _actor(body)
    retry = verify_user_delegation(
        _delegation(
            signer, body,
            root=original["root_request_id"],
            changes={"attempt_id": str(uuid.uuid4())},
        ),
        public_key=signer.public_key(),
        key_id=_KID,
        model="auto/code",
        request_path="/v1/chat/completions",
        request_body=body,
    )
    result = qualify_strict_single_native_task_shape(
        verified_actor=retry, original_request_body=body,
    )
    assert result["expected_native_task_count"] == 1
    assert result["actual_native_task_count_observed"] is None
    assert result["native_execution_attested"] is False
