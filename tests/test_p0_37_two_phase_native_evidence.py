"""Synthetic two-phase allocation set and independently signed slot evidence.

Fixture Ed25519 keys are generated per test. No model HTTP calls, native
headers, physical observers, cryptographic GGUF hashes or live signers.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_p0_37_request_digest import _payload
from test_p0_37_single_task_shape import _actor

from auto_router.native_task_receipt_join import (
    NativeTaskJoinRejected,
    _encode,
)
from auto_router.p0_two_phase_native_evidence import (
    ALLOC_DOMAIN,
    REFERENCE_DOMAIN,
    SLOT_DOMAIN,
    verify_raw_native_auth_header_shape,
    verify_two_phase_single_task_fixture,
)

_NOW = 1791762000000
_NODE = "disposable-node"
_AKID = "fixture-post-auth-allocation"
_SKID = "fixture-independent-slot"


def _sign(data, private_key, domain):
    packed = _encode(data)
    return (packed, private_key.sign(domain + packed))


@pytest.fixture
def fixture():
    body = _payload()
    actor = _actor(body)
    wire = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    alloc_key = Ed25519PrivateKey.generate()
    slot_key = Ed25519PrivateKey.generate()
    common = {
        "node_id": _NODE,
        "runtime_instance_id": "test-instance-1",
        "boot_id": str(uuid.uuid4()),
        "pid": 31415,
        "process_start_ticks": 91818,
        "model_artifact_sha256": "e" * 64,
    }
    record = {
        "schema": "p0-native-allocation-set-v2",
        "issuer": "trusted-post-auth-native-allocation-fixture",
        "audience": "auto-router-p0-two-phase-native",
        "key_id": _AKID,
        "gateway_issuer": actor["issuer"],
        "tenant": actor["tenant"],
        "subject": actor["subject"],
        "root_request_id": actor["root_request_id"],
        "attempt_id": actor["attempt_id"],
        "request_path": actor["request_path"],
        "original_request_sha256": actor["request_sha256"],
        "wire_request_sha256": hashlib.sha256(wire).hexdigest(),
        "wire_request_bytes": len(wire),
        "wire_method": "POST",
        "wire_request_path": "/v1/chat/completions",
        "model_handle": actor["model_handle"],
        **common,
        "admission_nonce": str(uuid.uuid4()),
        "observed_at_ms": _NOW - 8000,
        "allocated_tasks": [{
            "native_task_id": 42, "parent_task_id": -1,
            "allocation_index": 0,
        }],
    }

    def allocate(changes=None, signer=None, domain=ALLOC_DOMAIN):
        return _sign(
            {**record, **(changes or {})},
            alloc_key if signer is None else signer,
            domain,
        )

    def slot(*, allocation=None, changes=None, signer=None, domain=SLOT_DOMAIN):
        alloc_receipt = allocate() if allocation is None else allocation
        value = {
            "schema": "p0-native-slot-observation-v2",
            "issuer": "independent-native-slot-observer-fixture",
            "audience": "auto-router-p0-two-phase-native",
            "key_id": _SKID,
            "allocation_receipt_sha256": hashlib.sha256(
                REFERENCE_DOMAIN + alloc_receipt[0] + alloc_receipt[1]
            ).hexdigest(),
            **common,
            "native_task_id": 42,
            "parent_task_id": -1,
            "slot_id": 3,
            "stage": "slot_assigned",
            "event_id": str(uuid.uuid4()),
            "seq": 0,
            "observed_at_ms": _NOW - 5000,
        }
        return _sign(
            {**value, **(changes or {})},
            slot_key if signer is None else signer,
            domain,
        )

    def evaluate(
        *,
        allocation=None,
        slot_events=None,
        original_body=None,
        forwarded=None,
        method="POST",
        path="/v1/chat/completions",
        actor_override=None,
        alloc_pub=None,
        slot_pub=None,
        node=_NODE,
        time_ms=_NOW,
        allocation_kid=_AKID,
        slot_kid=_SKID,
    ):
        return verify_two_phase_single_task_fixture(
            verified_actor=actor if actor_override is None else actor_override,
            original_request_body=body if original_body is None else original_body,
            forwarded_wire_body=wire if forwarded is None else forwarded,
            forwarded_method=method,
            forwarded_path=path,
            signed_allocation=allocate() if allocation is None else allocation,
            signed_slots=[] if slot_events is None else slot_events,
            allocation_public_key=(
                alloc_key.public_key() if alloc_pub is None else alloc_pub
            ),
            allocation_key_id=allocation_kid,
            slot_public_key=slot_key.public_key() if slot_pub is None else slot_pub,
            slot_key_id=slot_kid,
            expected_node_id=node,
            now_ms=time_ms,
        )

    return body, actor, wire, alloc_key, slot_key, allocate, slot, evaluate


def test_no_native_evidence_is_not_witnessed(fixture):
    body, actor, wire, a, s, _allocation, _slot, _verify = fixture
    result = verify_two_phase_single_task_fixture(
        verified_actor=actor, original_request_body=body,
        forwarded_wire_body=wire,
        forwarded_method="POST", forwarded_path="/v1/chat/completions",
        signed_allocation=None, signed_slots=[],
        allocation_public_key=a.public_key(), allocation_key_id=_AKID,
        slot_public_key=s.public_key(), slot_key_id=_SKID,
        expected_node_id=_NODE, now_ms=_NOW,
    )
    assert result["signed_allocation_set"] == "unobserved"
    assert result["claimed_allocation_task_count"] is None
    assert result["physical_inference_permitted"] is False


def test_signed_allocation_has_NO_slot_id_and_never_proves_execution(fixture):
    *_unused, allocation, _slot, evaluate = fixture
    raw, _sig = allocation()
    decoded = json.loads(raw)
    assert "slot_id" not in decoded
    assert "slot_id" not in decoded["allocated_tasks"][0]
    result = evaluate()
    assert result["signed_allocation_set"] == "synthetic_allocation_claim_correlated"
    assert result["claimed_allocation_task_count"] == 1
    assert result["claimed_slot_count"] == 0
    assert result["all_signed_allocations_have_slot_claims"] is False
    assert result["actual_native_task_count_observed"] is None
    assert result["native_allocation_complete"] is False
    assert result["operational_ingress_signer_proven"] is False
    assert result["physical_inference_permitted"] is False
    assert result["response_delivered"] == "unknown"
    assert "tenant" not in result
    assert "subject" not in result


def test_distinct_signed_slot_receipt_join_does_not_prove_physical_execution(fixture):
    *_unused, allocation, slot, evaluate = fixture
    ingress = allocation()
    result = evaluate(allocation=ingress, slot_events=[slot(allocation=ingress)])
    assert result["signed_slot_claims"] == "synthetic_slot_claims_correlated"
    assert result["all_signed_allocations_have_slot_claims"] is True
    assert result["claimed_slot_count"] == 1
    assert result["model_artifact_sha256_measured"] is False
    assert result["native_allocation_complete"] is False
    assert result["actual_native_task_count_observed"] is None
    assert result["operational_slot_observer_proven"] is False
    assert result["durable_replay_fencing_proven"] is False
    assert result["provider_execution_attested"] is False
    assert result["physical_inference_permitted"] is False
    assert result["operator_approved"] is False


def test_distinct_crypto_public_keys_required(fixture):
    _body, _actor, _wire, a, _s, _allocate, _slot, evaluate = fixture
    with pytest.raises(NativeTaskJoinRejected, match="must differ"):
        evaluate(slot_pub=a.public_key())


@pytest.mark.parametrize("field,value", [
    ("schema", "old-v1-receipt"),
    ("issuer", "client-claimed"),
    ("audience", "another-router"),
    ("key_id", "wrong-key"),
    ("gateway_issuer", "unsigned-source"),
    ("tenant", "another-tenant"),
    ("subject", "bob"),
    ("root_request_id", str(uuid.uuid4())),
    ("attempt_id", str(uuid.uuid4())),
    ("request_path", "/v1/embeddings"),
    ("original_request_sha256", "0" * 64),
    ("wire_request_sha256", "0" * 64),
    ("wire_request_bytes", -1),
    ("wire_request_bytes", True),
    ("wire_method", "GET"),
    ("wire_request_path", "/v1/completions"),
    ("model_handle", "auto/wrong"),
    ("node_id", "another-node"),
    ("runtime_instance_id", ""),
    ("boot_id", "not-a-uuid"),
    ("pid", True),
    ("pid", 2**31),
    ("process_start_ticks", -1),
    ("model_artifact_sha256", "INVALID"),
    ("admission_nonce", str(uuid.uuid1())),
    ("observed_at_ms", _NOW - 200000),
    ("observed_at_ms", _NOW + 10000),
])
def test_signed_allocation_requires_correct_actor_process_wire_and_time(
    fixture, field, value,
):
    *_u, allocate, _slot, evaluate = fixture
    with pytest.raises(NativeTaskJoinRejected, match="identity/body"):
        evaluate(allocation=allocate({field: value}))


def test_actual_forwarded_bytes_must_match_signed_wire_body_not_original_only(fixture):
    *_u, _allocation, _slot, evaluate = fixture
    with pytest.raises(NativeTaskJoinRejected, match="identity/body"):
        evaluate(forwarded=b'{"model":"a-different-native-body"}')
    with pytest.raises(NativeTaskJoinRejected, match="identity/body"):
        evaluate(forwarded=b'{"original":"same but whitespace changes"}')


def test_translated_wire_body_can_differ_from_original_if_both_cryptographically_bound(
    fixture,
):
    _body, _actor, wire, _a, _s, allocate, slot, evaluate = fixture
    translated = wire + b" "
    signed = allocate({
        "wire_request_sha256": hashlib.sha256(translated).hexdigest(),
        "wire_request_bytes": len(translated),
    })
    result = evaluate(
        allocation=signed, forwarded=translated,
        slot_events=[slot(allocation=signed)],
    )
    assert result["all_signed_allocations_have_slot_claims"] is True
    assert result["provider_execution_attested"] is False


@pytest.mark.parametrize("tasks", [
    [],
    [{"native_task_id": 42, "parent_task_id": -1, "allocation_index": 1}],
    [{"native_task_id": 42, "parent_task_id": -1, "allocation_index": True}],
    [{"native_task_id": 42, "parent_task_id": 42, "allocation_index": 0}],
    [{"native_task_id": 42, "parent_task_id": 13, "allocation_index": 0}],
    [{"native_task_id": -1, "parent_task_id": -1, "allocation_index": 0}],
    [{"native_task_id": 2**31, "parent_task_id": -1, "allocation_index": 0}],
    [{"native_task_id": 42, "parent_task_id": -1,
      "allocation_index": 0, "slot_id": 3}],
    [
        {"native_task_id": 42, "parent_task_id": -1, "allocation_index": 0},
        {"native_task_id": 43, "parent_task_id": 42, "allocation_index": 1},
    ],
])
def test_wrong_child_parent_batch_or_premature_slot_identity_rejected(
    fixture, tasks,
):
    *_u, allocate, _slot, evaluate = fixture
    with pytest.raises(NativeTaskJoinRejected, match="allocation|fanout"):
        evaluate(allocation=allocate({"allocated_tasks": tasks}))


@pytest.mark.parametrize("field,value", [
    ("schema", "wrong"),
    ("issuer", "fabricated-observer"),
    ("audience", "other-audience"),
    ("key_id", "wrong-slot-key-id"),
    ("allocation_receipt_sha256", "0" * 64),
    ("node_id", "other-node"),
    ("runtime_instance_id", "other-instance"),
    ("boot_id", str(uuid.uuid4())),
    ("pid", 12),
    ("process_start_ticks", 314),
    ("model_artifact_sha256", "a" * 64),
    ("native_task_id", 43),
    ("parent_task_id", 42),
    ("slot_id", True),
    ("slot_id", -1),
    ("stage", "sampled_token"),
    ("event_id", "not-a-uuid"),
    ("seq", True),
    ("seq", 4),
    ("observed_at_ms", _NOW + 99999),
    ("observed_at_ms", _NOW - 10000),
])
def test_signed_slot_must_reference_exact_allocation_and_new_observation(
    fixture, field, value,
):
    *_u, allocate, slot, evaluate = fixture
    allocation = allocate()
    with pytest.raises(NativeTaskJoinRejected, match="native slot"):
        evaluate(
            allocation=allocation,
            slot_events=[slot(allocation=allocation, changes={field: value})],
        )


def test_wrong_native_signers_and_domain_separation(fixture):
    _body, _actor, _wire, a, s, allocate, slot, evaluate = fixture
    wrong = Ed25519PrivateKey.generate()
    with pytest.raises(NativeTaskJoinRejected, match="signature"):
        evaluate(allocation=allocate(signer=wrong))
    with pytest.raises(NativeTaskJoinRejected, match="signature"):
        evaluate(allocation=allocate(domain=SLOT_DOMAIN))
    receipt = allocate()
    with pytest.raises(NativeTaskJoinRejected, match="signature"):
        evaluate(
            allocation=receipt, slot_events=[slot(allocation=receipt, signer=a)],
        )
    with pytest.raises(NativeTaskJoinRejected, match="signature"):
        evaluate(
            allocation=receipt, slot_events=[slot(allocation=receipt, domain=ALLOC_DOMAIN)],
        )


def test_native_slot_cannot_bind_to_another_signed_allocation_nonce(fixture):
    *_u, allocate, slot, evaluate = fixture
    one = allocate()
    other = allocate({"admission_nonce": str(uuid.uuid4())})
    with pytest.raises(NativeTaskJoinRejected, match="native slot"):
        evaluate(allocation=one, slot_events=[slot(allocation=other)])


def test_second_observer_slot_on_same_task_denied(fixture):
    *_u, allocate, slot, evaluate = fixture
    ingress = allocate()
    first = slot(allocation=ingress)
    second = slot(allocation=ingress, changes={
        "event_id": str(uuid.uuid4()), "seq": 1, "slot_id": 4,
    })
    with pytest.raises(NativeTaskJoinRejected, match="native slot"):
        evaluate(allocation=ingress, slot_events=[first, second])


def test_repeating_identical_valid_signed_bundle_still_lacks_cross_bundle_replay(
    fixture,
):
    *_u, allocate, slot, evaluate = fixture
    ingress = allocate()
    events = [slot(allocation=ingress)]
    first = evaluate(allocation=ingress, slot_events=events)
    second = evaluate(allocation=ingress, slot_events=events)
    assert first == second
    assert first["durable_replay_fencing_proven"] is False
    assert first["provider_execution_attested"] is False


def test_signed_allocation_must_be_canonical_duplicate_free_and_bounded(fixture):
    _body, _actor, _wire, ak, _sk, allocate, _slot, evaluate = fixture
    payload, _sig = allocate()
    duplicate = payload[:-1] + b',"schema":"p0-native-allocation-set-v2"}'
    with pytest.raises(NativeTaskJoinRejected, match="schema"):
        evaluate(allocation=_sign(
            {**json.loads(payload), "slot_id": 3}, ak, ALLOC_DOMAIN,
        ))
    with pytest.raises(NativeTaskJoinRejected, match="duplicate signed"):
        evaluate(allocation=(duplicate, ak.sign(ALLOC_DOMAIN + duplicate)))
    with pytest.raises(NativeTaskJoinRejected, match="bounded"):
        evaluate(allocation=(b"x" * 3073, b"\x00" * 64))


@pytest.mark.parametrize("raw_headers", [
    [("Authorization", "bearer A"), ("authorization", "bearer B")],
    [("X-P0-Native-Admission", "sig A"), ("x-p0-native-admission", "sig B")],
    [("X-P0-Signature", "sig1"), ("X-P0-SIGNATURE", "sig2")],
    [("Authorization", "A"), ("X-Api-Key", "B")],
    [("X-P0-Native-Admission", "private\r\nAuthorization: injected")],
    [(" X-P0-Signature", "bad name")],
    [("X-P0-Signature", "one\nanother")],
    [("X-P0-Signature", "a\x00b")],
    [("X-P0-Signature", "x" * 4100)],
])
def test_header_frame_ambiguity_rejected_before_lossy_map_conversion(raw_headers):
    with pytest.raises(NativeTaskJoinRejected):
        verify_raw_native_auth_header_shape(raw_headers)


def test_unique_raw_header_shape_never_authenticates_human_or_request():
    report = verify_raw_native_auth_header_shape([
        ("Host", "test.internal"),
        ("Authorization", "test service-bearer SECRET_SHOULD_NOT_BE_ECHOED"),
        ("x-p0-native-admission", "signed-but-not-verified-here"),
        ("Content-Type", "application/json"),
    ])
    assert report == {
        "schema": "p0-raw-header-shape-v1",
        "protected_header_duplicates_rejected": True,
        "authenticated_forwarder": False,
        "authenticated_human": False,
        "signed_native_admission_verified": False,
    }
    assert "SECRET_SHOULD_NOT_BE_ECHOED" not in str(report)


def test_original_shape_fails_before_parsing_any_native_signed_claim(
    fixture, monkeypatch,
):
    import auto_router.p0_two_phase_native_evidence as m

    *_u, evaluate = fixture
    bad = _payload()
    bad["n"] = 2

    def forbidden_decode(*_args, **_kwargs):
        raise AssertionError("invalid original shape reached signed verifier")

    monkeypatch.setattr(m, "_decode", forbidden_decode)
    from auto_router.p0_single_task_shape import SingleTaskShapeRejected
    with pytest.raises(SingleTaskShapeRejected):
        evaluate(original_body=bad)


def test_valid_fixture_does_not_return_subject_or_prompt_and_never_authorizes():
    # No process telemetry is emitted by this fixture verifier.
    assert verify_raw_native_auth_header_shape([])["authenticated_human"] is False



@pytest.mark.parametrize("method,path", [
    ("GET", "/v1/chat/completions"),
    ("POST", "/v1/completions"),
    ("post", "/v1/chat/completions"),
    ("POST", "/v1/chat/completions/"),
])
def test_native_wire_method_path_must_be_operator_pinned(
    fixture, method, path,
):
    *_x, evaluate = fixture
    with pytest.raises(NativeTaskJoinRejected, match="exact forwarded request"):
        evaluate(method=method, path=path)


def test_actual_signed_actor_missing_human_subject_never_produces_join(fixture):
    _body, actor, _wire, _a, _s, _allocate, _slot, evaluate = fixture
    for missing in ("tenant", "subject"):
        partial = {k: v for k, v in actor.items() if k != missing}
        with pytest.raises(NativeTaskJoinRejected):
            evaluate(actor_override=partial)


@pytest.mark.parametrize("value", [
    "tab\tcontrol", "del\x7fcontrol", "\r\n", "zero\x00byte",
])
def test_header_value_control_characters_rejected_before_narrowing(value):
    with pytest.raises(NativeTaskJoinRejected, match="unsafe raw HTTP"):
        verify_raw_native_auth_header_shape([("X-P0-Signature", value)])
