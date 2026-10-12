"""Strict shape must precede synthetic native task receipt correlation.

All signing is with ephemeral fixture keys; no llama-server is contacted.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from test_p0_37_native_task_receipt_join import (
    _INGRESS_DOMAIN,
    _INGRESS_KID,
    _JOIN_DOMAIN,
    _NATIVE_DOMAIN,
    _NATIVE_KID,
    _NODE,
    _NOW_MS,
    _sequence,
    _sign,
)
from test_p0_37_native_task_receipt_join import (
    bundle as _upstream_bundle_fixture,
)
from test_p0_37_request_digest import _payload
from test_p0_37_single_task_shape import _actor

from auto_router.native_task_receipt_join import NativeTaskJoinRejected
from auto_router.p0_single_task_canary_join import (
    correlate_strict_single_task_canary_fixture,
)
from auto_router.p0_single_task_shape import SingleTaskShapeRejected


@pytest.fixture(name="bundle")
def _native_bundle_fixture():
    # Reuse the existing ephemeral two-signer fixture without requiring
    # a shared global conftest or a live service.
    return _upstream_bundle_fixture.__wrapped__()


def _evaluate(bundle_data, body=None, actor_override=None, *,
              events_mode="terminal", ingress_override=None):
    actor, post_auth, observer, ingress_fn, event_fn, _check = bundle_data
    ingress = ingress_fn() if ingress_override is None else ingress_override
    events = (
        _sequence(event_fn, ingress=ingress)
        if events_mode == "terminal" else
        [event_fn("slot_assigned", 0, ingress=ingress)]
        if events_mode == "partial" else []
    )
    return correlate_strict_single_task_canary_fixture(
        verified_actor=actor if actor_override is None else actor_override,
        original_request_body=_payload() if body is None else body,
        signed_ingress=ingress,
        signed_native_events=events,
        ingress_public_key=post_auth.public_key(),
        ingress_key_id=_INGRESS_KID,
        native_public_key=observer.public_key(),
        native_key_id=_NATIVE_KID,
        expected_node_id=_NODE,
        now_ms=_NOW_MS,
    )


def test_exact_v2_request_can_correlate_fixture_but_never_execute(bundle):
    result = _evaluate(bundle)
    assert result["canary_shape"] == "single_plain_text_chat_candidate"
    assert result["admission_correlation"] == "signed_fixture_ingress_correlated"
    assert result["native_lifecycle_correlation"] == "signed_fixture_terminal_claim"
    assert result["native_terminal_claim_correlated"] is True
    assert result["expected_native_task_count"] == 1
    assert result["actual_native_task_count_observed"] is None
    assert result["native_parent_child_lineage_proven"] is False
    assert result["native_allocation_complete"] is False
    assert result["provider_execution_attested"] is False
    assert result["physical_provider_proof"] is False
    assert result["physical_inference_permitted"] is False
    assert result["operator_approved"] is False
    assert result["response_delivered"] == "unknown"


@pytest.mark.parametrize("stage_mode", ["partial", "none"])
def test_incomplete_native_fixtures_remain_explicitly_unobserved(bundle, stage_mode):
    result = _evaluate(bundle, events_mode=stage_mode)
    assert result["native_terminal_claim_correlated"] is False
    assert result["actual_native_task_count_observed"] is None
    assert result["provider_execution_attested"] is False


@pytest.mark.parametrize("extra", [
    {"n": 1}, {"n": 2}, {"best_of": 4},
    {"prompt": ["a", "b"]},
    {"messages": [
        {"role": "user", "content": "prompt A"},
        {"role": "user", "content": "prompt B"},
    ]},
    {"parallel_tool_calls": True},
    {"tools": [{"type": "function"}]},
    {"metadata": {"subject": "pretend alice"}},
    {"id_slot": 0},
    {"stream": "true"},
])
def test_signed_ambiguous_shapes_cannot_reach_signed_native_join(
    bundle, extra, monkeypatch,
):
    import auto_router.p0_single_task_canary_join as join_module

    body = {**_payload(), **extra}
    actor = _actor(body)

    def native_join_must_not_run(**_kwargs):
        raise AssertionError("invalid canary shape reached native signature join")

    monkeypatch.setattr(
        join_module, "correlate_native_task_join", native_join_must_not_run
    )
    with pytest.raises(SingleTaskShapeRejected):
        _evaluate(bundle, body=body, actor_override=actor)


def test_original_prompt_mutation_denied_before_valid_native_fixture(bundle):
    request = _payload()
    request["messages"] = [
        {"role": "user", "content": "someone swapped the prompt"},
    ]
    with pytest.raises(SingleTaskShapeRejected, match="signed request digest"):
        _evaluate(bundle, body=request)


def test_distinct_signed_human_cannot_reuse_one_ingress(bundle):
    bob = _actor(_payload())
    assert bob["attempt_id"] != bundle[0]["attempt_id"]
    with pytest.raises(NativeTaskJoinRejected, match="signed actor"):
        _evaluate(bundle, actor_override=bob)


def test_same_root_different_signed_retry_cannot_reuse_original_task(bundle):
    original = bundle[0]
    retry = {
        **original,
        "attempt_id": str(uuid.uuid4()),
    }
    assert retry["root_request_id"] == original["root_request_id"]
    with pytest.raises(NativeTaskJoinRejected, match="signed actor"):
        _evaluate(bundle, actor_override=retry)


def test_multiple_separately_signed_bundles_cannot_establish_unique_allocation(
    bundle,
):
    # The stateless join can verify a second internally consistent
    # task/slot bundle under the SAME signed actor. Only a future
    # independently controlled native allocation ledger can rule out
    # unobserved extra tasks/children and task-ID reuse.
    actor, ingress_key, native_key, ingress_fn, event_fn, _verify = bundle
    first = _evaluate(bundle)
    base = json.loads(ingress_fn()[0])
    base["native_task_id"] = 999
    base["slot_id"] = 8
    base["admission_nonce"] = str(uuid.uuid4())
    second_ingress = _sign(base, ingress_key, _INGRESS_DOMAIN)
    second_events = _sequence(
        event_fn, ingress=second_ingress,
    )
    # Reconstruct the observer fixture with matching second task
    # identity; otherwise cryptographic verifier correctly rejects.
    ref = hashlib.sha256(_JOIN_DOMAIN + second_ingress[0]).hexdigest()
    events = []
    for raw, _sig in second_events:
        e = json.loads(raw)
        e["native_task_id"] = 999
        e["slot_id"] = 8
        e["admission_receipt_sha256"] = ref
        events.append(_sign(e, native_key, _NATIVE_DOMAIN))

    result = correlate_strict_single_task_canary_fixture(
        verified_actor=actor, original_request_body=_payload(),
        signed_ingress=second_ingress, signed_native_events=events,
        ingress_public_key=ingress_key.public_key(),
        ingress_key_id=_INGRESS_KID,
        native_public_key=native_key.public_key(),
        native_key_id=_NATIVE_KID, expected_node_id=_NODE, now_ms=_NOW_MS,
    )
    assert first["native_terminal_claim_correlated"] is True
    assert result["native_terminal_claim_correlated"] is True
    assert first["native_allocation_complete"] is False
    assert result["native_allocation_complete"] is False
    assert result["cross_bundle_replay_verified"] is False
    assert result["provider_execution_attested"] is False
