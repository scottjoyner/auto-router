"""P0 #37 two distinct synthetic signers: post-auth admission + native task.

All signing is test-fixture only. A valid signature chain is NEVER
equivalent to a physical model executing a real human's request.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from test_p0_37_request_digest import _delegation, _payload, _post
from test_p0_37_verified_user_delegation import _KID, _app, _token

from auto_router.native_task_receipt_join import (
    _INGRESS_DOMAIN,
    _JOIN_DOMAIN,
    _NATIVE_DOMAIN,
    NativeTaskJoinRejected,
    correlate_native_task_join,
)
from auto_router.user_delegation import delegated_body_digest, verify_user_delegation

_NOW_MS = 1791762000000
_NODE = "xwing-test-only"
_INGRESS_KID = "native-ingress-fixture"
_NATIVE_KID = "native-observer-fixture"


def _sign(value: dict, secret: Ed25519PrivateKey, domain: bytes):
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    return payload, secret.sign(domain + payload)


@pytest.fixture
def bundle():
    gw = Ed25519PrivateKey.generate()
    post_auth = Ed25519PrivateKey.generate()
    observer = Ed25519PrivateKey.generate()
    body = _payload()
    actor = verify_user_delegation(
        _delegation(gw, body), public_key=gw.public_key(), key_id=_KID,
        model="auto/code", request_path="/v1/chat/completions",
        request_body=body,
    )
    machine = {
        "node_id": _NODE,
        "runtime_instance_id": "fixture-runtime-1",
        "boot_id": str(uuid.uuid4()),
        "pid": 931526,
        "process_start_ticks": 8211850,
        "model_artifact_sha256": "b" * 64,
        "native_task_id": 42,
        "slot_id": 1,
    }
    original = {
        "schema": "p0-native-admitted-task-v1",
        "issuer": "post-auth-native-ingress-p0",
        "audience": "auto-router-p0-native-task-join",
        "key_id": _INGRESS_KID,
        **machine,
        "root_request_id": actor["root_request_id"],
        "attempt_id": actor["attempt_id"],
        "request_path": actor["request_path"],
        "request_sha256": actor["request_sha256"],
        "model_handle": actor["model_handle"],
        "admission_nonce": str(uuid.uuid4()),
        "observed_at_ms": _NOW_MS - 10_000,
    }
    def make_ingress(changes=None, signer=None, domain=_INGRESS_DOMAIN):
        return _sign(
            {**original, **(changes or {})},
            post_auth if signer is None else signer,
            domain,
        )

    def make_event(
        stage: str, seq: int, *, changes=None,
        ingress=None, signer=None, domain=_NATIVE_DOMAIN,
    ):
        receipt = make_ingress() if ingress is None else ingress
        value = {
            "schema": "p0-native-task-event-v1",
            "issuer": "separate-native-process-observer-p0",
            "audience": "auto-router-p0-native-task-join",
            "key_id": _NATIVE_KID,
            **machine,
            "admission_receipt_sha256": hashlib.sha256(
                _JOIN_DOMAIN + receipt[0]
            ).hexdigest(),
            "stage": stage,
            "event_id": str(uuid.uuid4()),
            "seq": seq,
            "observed_at_ms": _NOW_MS - 9000 + seq * 1000,
            "generated_tokens": 1 if stage == "sampled_token" else (
                0 if stage == "slot_assigned" else 2
            ),
        }
        return _sign(
            {**value, **(changes or {})},
            observer if signer is None else signer,
            domain,
        )

    def verify(
        events=(), *, ingress=None, actor_override=None,
        ingress_pub=None, observer_pub=None, ingress_kid=_INGRESS_KID,
        native_kid=_NATIVE_KID, node=_NODE, now=_NOW_MS,
    ):
        return correlate_native_task_join(
            verified_actor=actor if actor_override is None else actor_override,
            signed_ingress=make_ingress() if ingress is None else ingress,
            signed_native_events=events,
            ingress_public_key=post_auth.public_key() if ingress_pub is None else ingress_pub,
            ingress_key_id=ingress_kid,
            native_public_key=observer.public_key() if observer_pub is None else observer_pub,
            native_key_id=native_kid,
            expected_node_id=node,
            now_ms=now,
        )

    return actor, post_auth, observer, make_ingress, make_event, verify


def _sequence(make_event, *, ingress=None, terminal="final_result_enqueued"):
    return [
        make_event("slot_assigned", 0, ingress=ingress),
        make_event("sampled_token", 1, ingress=ingress),
        make_event(terminal, 2, ingress=ingress),
    ]


def test_no_ingress_and_no_events_refuses_any_proof(bundle):
    actor, a, b, _, _, _ = bundle
    result = correlate_native_task_join(
        verified_actor=actor, signed_ingress=None, signed_native_events=[],
        ingress_public_key=a.public_key(), ingress_key_id=_INGRESS_KID,
        native_public_key=b.public_key(), native_key_id=_NATIVE_KID,
        expected_node_id=_NODE, now_ms=_NOW_MS,
    )
    assert result["admission_correlation"] == "unobserved"
    assert result["provider_execution_attested"] is False
    assert result["cross_bundle_replay_verified"] is False


def test_signed_ingress_only_is_not_native_execution(bundle):
    *_items, verify = bundle
    result = verify([])
    assert result["admission_correlation"] == "signed_fixture_ingress_correlated"
    assert result["native_lifecycle_correlation"] == "unobserved"
    assert result["native_terminal_claim_correlated"] is False
    assert result["provider_execution_attested"] is False


@pytest.mark.parametrize("terminal", [
    "final_result_enqueued", "native_error", "native_cancelled",
])
def test_two_separately_signed_complete_bundles_remain_synthetic(
    bundle, terminal,
):
    actor, _a, _b, make_ingress, make_event, verify = bundle
    ingress = make_ingress()
    result = verify(
        _sequence(make_event, ingress=ingress, terminal=terminal),
        ingress=ingress,
    )
    assert result["admission_correlation"] == "signed_fixture_ingress_correlated"
    assert result["native_lifecycle_correlation"] == "signed_fixture_terminal_claim"
    assert result["native_terminal_claim_correlated"] is True
    assert result["signed_native_event_count"] == 3
    assert result["provider_execution_attested"] is False
    assert result["physical_provider_proof"] is False
    assert result["response_delivered"] == "unknown"
    assert result["first_token_proven"] is False
    assert result["encrypted_trace_custody_verified"] is False
    assert result["operational_signer_provenance_verified"] is False
    assert "tenant" not in result and "subject" not in result
    assert "native_task_id" not in result
    assert actor["subject"] not in str(result)


def test_native_result_without_any_sample_is_allowed_but_no_token_proof(bundle):
    _actor, _a, _b, ingress_fn, event, verify = bundle
    receipt = ingress_fn()
    result = verify(
        [
            event("slot_assigned", 0, ingress=receipt),
            event("final_result_enqueued", 1, ingress=receipt,
                  changes={"generated_tokens": 0}),
        ],
        ingress=receipt,
    )
    assert result["native_terminal_claim_correlated"] is True
    assert result["first_token_proven"] is False


def test_partial_native_sequence_cannot_infer_terminal(bundle):
    _actor, _a, _b, ingress_fn, event, verify = bundle
    ingress = ingress_fn()
    r = verify([event("slot_assigned", 0, ingress=ingress)], ingress=ingress)
    assert r["native_lifecycle_correlation"] == "signed_fixture_partial"
    assert r["native_terminal_claim_correlated"] is False


@pytest.mark.parametrize("changes", [
    {"root_request_id": str(uuid.uuid4())},
    {"attempt_id": str(uuid.uuid4())},
    {"request_path": "/v1/embeddings"},
    {"request_sha256": "0" * 64},
    {"model_handle": "auto/other"},
])
def test_signed_ingress_must_match_real_v2_gateway_actor(bundle, changes):
    _actor, _a, _b, ingress_fn, _event, verify = bundle
    with pytest.raises(NativeTaskJoinRejected, match="signed actor"):
        verify(ingress=ingress_fn(changes))


@pytest.mark.parametrize("field,value", [
    ("node_id", "untrusted-node"),
    ("runtime_instance_id", ""),
    ("boot_id", "not-a-uuid"),
    ("pid", True),
    ("process_start_ticks", -1),
    ("model_artifact_sha256", "not-a-digest"),
    ("native_task_id", -1),
    ("slot_id", True),
    ("admission_nonce", str(uuid.uuid1())),
    ("observed_at_ms", _NOW_MS - 999999),
    ("issuer", "user-supplied-ingress"),
    ("audience", "someone-else"),
    ("key_id", "other-key"),
])
def test_invalid_signed_ingress_identity_is_rejected(bundle, field, value):
    _actor, _a, _b, ingress_fn, _event, verify = bundle
    with pytest.raises(NativeTaskJoinRejected, match="native ingress identity invalid"):
        verify(ingress=ingress_fn({field: value}))


@pytest.mark.parametrize("field,value", [
    ("native_task_id", 43),
    ("slot_id", 2),
    ("boot_id", str(uuid.uuid4())),
    ("pid", 8188),
    ("process_start_ticks", 8088),
    ("model_artifact_sha256", "c" * 64),
    ("runtime_instance_id", "other-runtime"),
    ("node_id", "other-node"),
])
def test_native_observer_cannot_substitute_process_or_task(
    bundle, field, value,
):
    _actor, _a, _b, ingress_fn, event, verify = bundle
    ingress = ingress_fn()
    with pytest.raises(NativeTaskJoinRejected, match="identity|substituted"):
        verify(
            [event("slot_assigned", 0, changes={field: value}, ingress=ingress)],
            ingress=ingress,
        )


def test_signed_native_event_bound_to_exact_ingress_nonce(bundle):
    _actor, _a, _b, ingress_fn, event, verify = bundle
    valid_ingress = ingress_fn()
    other_ingress = ingress_fn({"admission_nonce": str(uuid.uuid4())})
    with pytest.raises(NativeTaskJoinRejected, match="not bound to signed ingress"):
        verify(
            [event("slot_assigned", 0, ingress=other_ingress)],
            ingress=valid_ingress,
        )


def test_separately_pinned_authorities_must_not_share_one_key(bundle):
    _actor, post_auth, _observer, _ingress, _event, verify = bundle
    with pytest.raises(NativeTaskJoinRejected, match="keys must differ"):
        verify(observer_pub=post_auth.public_key())


def test_wrong_signatures_and_wrong_signing_domains_rejected(bundle):
    _actor, post_auth, _observer, ingress_fn, event, verify = bundle
    wrong_key = Ed25519PrivateKey.generate()
    with pytest.raises(NativeTaskJoinRejected, match="signature invalid"):
        verify(ingress=ingress_fn(signer=wrong_key))
    with pytest.raises(NativeTaskJoinRejected, match="signature invalid"):
        verify(ingress=ingress_fn(domain=_NATIVE_DOMAIN))
    ingress = ingress_fn()
    with pytest.raises(NativeTaskJoinRejected, match="signature invalid"):
        verify([event("slot_assigned", 0, ingress=ingress, signer=post_auth)],
               ingress=ingress)
    with pytest.raises(NativeTaskJoinRejected, match="signature invalid"):
        verify([event("slot_assigned", 0, ingress=ingress, domain=_INGRESS_DOMAIN)],
               ingress=ingress)


@pytest.mark.parametrize("field,value", [
    ("key_id", "forged-observer"),
    ("audience", "untrusted-audience"),
    ("issuer", "service-header-value"),
    ("schema", "v0"),
    ("native_task_id", True),
    ("pid", 0),
])
def test_observer_field_and_identity_spoofing_denied(bundle, field, value):
    _actor, _a, _b, ingress_fn, event, verify = bundle
    ingress = ingress_fn()
    with pytest.raises(NativeTaskJoinRejected, match="observer identity invalid"):
        verify(
            [event("slot_assigned", 0, ingress=ingress, changes={field: value})],
            ingress=ingress,
        )


@pytest.mark.parametrize("field,value", [
    ("admission_receipt_sha256", "0" * 64),
    ("admission_receipt_sha256", "not-hex"),
])
def test_wrong_ingress_digest_denied(bundle, field, value):
    _actor, _a, _b, ingress_fn, event, verify = bundle
    ingress = ingress_fn()
    with pytest.raises(NativeTaskJoinRejected, match="not bound"):
        verify(
            [event("slot_assigned", 0, ingress=ingress, changes={field: value})],
            ingress=ingress,
        )


@pytest.mark.parametrize("changes", [
    {"seq": 1},
    {"seq": True},
    {"event_id": "not-uuid"},
    {"observed_at_ms": _NOW_MS - 200000},
    {"observed_at_ms": _NOW_MS + 10000},
    {"generated_tokens": True},
    {"generated_tokens": -1},
    {"stage": "http_chunk"},
])
def test_event_bounds_freshness_or_order_invalid(bundle, changes):
    _actor, _a, _b, ingress_fn, event, verify = bundle
    ingress = ingress_fn()
    with pytest.raises(NativeTaskJoinRejected, match="sequence, freshness or budget"):
        verify(
            [event("slot_assigned", 0, changes=changes, ingress=ingress)],
            ingress=ingress,
        )


def test_first_event_must_be_slot_assigned_with_no_tokens(bundle):
    _actor, _a, _b, ingress_fn, event, verify = bundle
    ingress = ingress_fn()
    with pytest.raises(NativeTaskJoinRejected, match="slot assignment"):
        verify([event("sampled_token", 0, ingress=ingress)], ingress=ingress)
    with pytest.raises(NativeTaskJoinRejected, match="slot assignment"):
        verify([event("slot_assigned", 0, ingress=ingress,
                      changes={"generated_tokens": 1})], ingress=ingress)


def test_observer_event_replay_inside_bundle_denied(bundle):
    _actor, _a, _b, ingress_fn, event, verify = bundle
    ingress = ingress_fn()
    first = event("slot_assigned", 0, ingress=ingress)
    duplicate = event("sampled_token", 1, ingress=ingress, changes={
        "event_id": json.loads(first[0])["event_id"],
    })
    with pytest.raises(NativeTaskJoinRejected, match="sequence, freshness or budget"):
        verify([first, duplicate], ingress=ingress)


def test_duplicate_native_slot_or_terminal_after_terminal_denied(bundle):
    _actor, _a, _b, ingress_fn, event, verify = bundle
    ingress = ingress_fn()
    first = event("slot_assigned", 0, ingress=ingress)
    with pytest.raises(NativeTaskJoinRejected, match="duplicate native slot"):
        verify([first, event("slot_assigned", 1, ingress=ingress)], ingress=ingress)
    with pytest.raises(NativeTaskJoinRejected, match="after terminal"):
        verify(
            [first,
             event("native_cancelled", 1, ingress=ingress),
             event("final_result_enqueued", 2, ingress=ingress)],
            ingress=ingress,
        )


def test_token_counter_and_terminal_order_regression_denied(bundle):
    _actor, _a, _b, ingress_fn, event, verify = bundle
    ingress = ingress_fn()
    events = [
        event("slot_assigned", 0, ingress=ingress),
        event("sampled_token", 1, ingress=ingress, changes={"generated_tokens": 3}),
        event("sampled_token", 2, ingress=ingress, changes={"generated_tokens": 2}),
    ]
    with pytest.raises(NativeTaskJoinRejected, match="not increasing"):
        verify(events, ingress=ingress)
    events[2] = event("final_result_enqueued", 2, ingress=ingress,
                      changes={"generated_tokens": 2})
    with pytest.raises(NativeTaskJoinRejected, match="regressed"):
        verify(events, ingress=ingress)


def test_out_of_order_native_event_time_denied(bundle):
    _actor, _a, _b, ingress_fn, event, verify = bundle
    ingress = ingress_fn()
    events = [
        event("slot_assigned", 0, ingress=ingress),
        event("sampled_token", 1, ingress=ingress, changes={
            "observed_at_ms": _NOW_MS - 9500,
        }),
    ]
    with pytest.raises(NativeTaskJoinRejected, match="sequence, freshness or budget"):
        verify(events, ingress=ingress)


def test_signed_event_without_signed_ingress_denied(bundle):
    actor, ingress_key, observer_key, _ingress_fn, event, _verify = bundle
    with pytest.raises(NativeTaskJoinRejected, match="without admitted task"):
        correlate_native_task_join(
            verified_actor=actor, signed_ingress=None,
            signed_native_events=[event("slot_assigned", 0)],
            ingress_public_key=ingress_key.public_key(),
            ingress_key_id=_INGRESS_KID,
            native_public_key=observer_key.public_key(),
            native_key_id=_NATIVE_KID,
            expected_node_id=_NODE, now_ms=_NOW_MS,
        )


def test_actor_cannot_be_reconstructed_from_caller_subject_label(bundle):
    actor, _a, _b, _ingress, _event, verify = bundle
    with pytest.raises(NativeTaskJoinRejected, match="v2 request-scoped actor"):
        verify(actor_override={**actor, "issuer": "caller-json-metadata"})


def test_signed_fixture_replay_across_invocations_is_not_prevented_or_promoted(bundle):
    _actor, _a, _b, ingress_fn, event, verify = bundle
    ingress = ingress_fn()
    events = _sequence(event, ingress=ingress)
    once = verify(events, ingress=ingress)
    twice = verify(events, ingress=ingress)
    assert once == twice
    assert once["cross_bundle_replay_verified"] is False
    assert once["provider_execution_attested"] is False


def test_signed_json_must_be_canonical_without_duplicate_fields(bundle):
    _actor, ingress_key, _observer, ingress_fn, _event, verify = bundle
    body, _sig = ingress_fn()
    duplicate = body[:-1] + b',"native_task_id":42}'
    with pytest.raises(NativeTaskJoinRejected, match="duplicate signed evidence"):
        verify(ingress=(duplicate, ingress_key.sign(_INGRESS_DOMAIN + duplicate)))
    noncanonical = b" " + body
    with pytest.raises(NativeTaskJoinRejected, match="noncanonical"):
        verify(ingress=(
            noncanonical, ingress_key.sign(_INGRESS_DOMAIN + noncanonical),
        ))


def test_oversized_or_ambiguous_evidence_rejected(bundle):
    _actor, _a, _b, ingress_fn, event, verify = bundle
    ingress = ingress_fn()
    with pytest.raises(NativeTaskJoinRejected, match="bounded signed evidence"):
        verify(ingress=(b"x" * 3073, b"\x00" * 64))
    with pytest.raises(NativeTaskJoinRejected, match="budget exceeded"):
        verify(
            [event("slot_assigned", i, ingress=ingress) for i in range(9)],
            ingress=ingress,
        )


def test_ingress_nonce_not_binding_to_node_runtime_from_user_labels(bundle):
    actor, _a, _b, ingress_fn, event, verify = bundle
    ingress = ingress_fn()
    forged = {**actor, "node_id": "user-selected-node", "native_task_id": 42}
    r = verify([], ingress=ingress, actor_override=forged)
    assert r["provider_execution_attested"] is False
    assert r["operational_signer_provenance_verified"] is False



def _actual_signed_fixture_actor(signer, *, subject, root=None, attempt=None):
    """Independently validate each synthetic subject's genuine EdDSA JWT."""
    body = _payload()
    path = "/v1/chat/completions"
    token = _token(
        signer, subject, root=root, attempt=attempt,
        claims_changes={
            "schema": "p0-user-shadow-v2",
            "request_path": path,
            "request_sha256": delegated_body_digest(path, body),
        },
    )
    return verify_user_delegation(
        token, public_key=signer.public_key(), key_id=_KID,
        model="auto/code", request_path=path, request_body=body,
    )


def test_distinct_signed_humans_cannot_reuse_other_native_task_bundle(bundle):
    alice, _admission_key, _observer, ingress_fn, event, verify = bundle
    signer = Ed25519PrivateKey.generate()
    bob = _actual_signed_fixture_actor(signer, subject="bob")
    assert bob["subject"] != alice["subject"]
    assert bob["tenant"] == alice["tenant"]
    assert bob["request_sha256"] == alice["request_sha256"]
    admission = ingress_fn()
    events = _sequence(event, ingress=admission)
    with pytest.raises(NativeTaskJoinRejected, match="signed actor"):
        verify(events, ingress=admission, actor_override=bob)
    assert verify(events, ingress=admission)["provider_execution_attested"] is False


def test_same_user_same_root_distinct_signed_retry_attempt_cannot_reuse_native_id(
    bundle,
):
    alice, _admission_key, _observer, ingress_fn, event, verify = bundle
    signer = Ed25519PrivateKey.generate()
    retry = _actual_signed_fixture_actor(
        signer,
        subject="alice",
        root=alice["root_request_id"],
        attempt=str(uuid.uuid4()),
    )
    assert retry["root_request_id"] == alice["root_request_id"]
    assert retry["attempt_id"] != alice["attempt_id"]
    assert retry["request_sha256"] == alice["request_sha256"]
    admission = ingress_fn()
    with pytest.raises(NativeTaskJoinRejected, match="signed actor"):
        verify(
            _sequence(event, ingress=admission),
            ingress=admission, actor_override=retry,
        )


def test_client_supplied_legacy_unsigned_user_label_is_not_ingress_authority(
    bundle,
):
    actor, _ingress_key, _observer, ingress_fn, _event, verify = bundle
    admission = ingress_fn()
    metadata = {**actor, "issuer": "X-User-from-client", "subject": "alice"}
    with pytest.raises(NativeTaskJoinRejected, match="v2 request-scoped actor"):
        verify([], ingress=admission, actor_override=metadata)


def test_multiple_native_sample_events_allow_monotonic_bounded_counts(bundle):
    _actor, _ingress_key, _observer, ingress_fn, event, verify = bundle
    admission = ingress_fn()
    events = [event("slot_assigned", 0, ingress=admission)]
    for seq, value in enumerate([1, 3, 5], start=1):
        events.append(
            event("sampled_token", seq, ingress=admission,
                  changes={"generated_tokens": value})
        )
    events.append(
        event("final_result_enqueued", 4, ingress=admission,
              changes={"generated_tokens": 5})
    )
    result = verify(events, ingress=admission)
    assert result["signed_native_event_count"] == 5
    assert result["native_terminal_claim_correlated"] is True
    assert result["provider_execution_attested"] is False
    assert result["first_token_proven"] is False



def test_actual_authenticating_asgi_boundary_feeds_fixture_native_task_join(
    tmp_path, monkeypatch, bundle,
):
    """Real router middleware authenticates two JWT test actors first.

    Only the downstream ingress/task observer receipts are synthetic.
    No provider socket, model process or production service is touched.
    """
    _fixture_actor, _a, _b, ingress_fn, event_fn, verify = bundle
    human_signer = Ed25519PrivateKey.generate()
    app, observed, _ledger = _app(
        tmp_path, monkeypatch, human_signer, require_body_digest=True,
    )
    body = _payload()
    alice_token = _delegation(human_signer, body)
    bob_token = _token(
        human_signer, "bob", claims_changes={
            "schema": "p0-user-shadow-v2",
            "request_path": "/v1/chat/completions",
            "request_sha256": delegated_body_digest("/v1/chat/completions", body),
        },
    )
    with TestClient(app) as client:
        alice_resp = _post(client, alice_token, body)
        bob_resp = _post(client, bob_token, body)
        tampered = _post(client, alice_token, {
            **body, "messages": [{"role": "user", "content": "tampered"}],
        })
    assert [alice_resp.status_code, bob_resp.status_code, tampered.status_code] == [
        200, 200, 401,
    ]
    assert len(observed) == 2
    alice_actor, bob_actor = (row["actor"] for row in observed)
    assert alice_actor["subject"] == "alice"
    assert bob_actor["subject"] == "bob"
    assert alice_actor["request_sha256"] == bob_actor["request_sha256"]
    assert alice_actor["root_request_id"] != bob_actor["root_request_id"]

    admitted = json.loads(ingress_fn()[0])
    for field in (
        "root_request_id", "attempt_id", "request_path", "request_sha256",
        "model_handle",
    ):
        admitted[field] = alice_actor[field]
    signed_ingress = _sign(admitted, _a, _INGRESS_DOMAIN)
    native_fixture_events = _sequence(event_fn, ingress=signed_ingress)
    result = verify(
        native_fixture_events, ingress=signed_ingress,
        actor_override=alice_actor,
    )
    assert result["native_terminal_claim_correlated"] is True
    assert result["provider_execution_attested"] is False
    with pytest.raises(NativeTaskJoinRejected, match="signed actor"):
        verify(
            native_fixture_events, ingress=signed_ingress,
            actor_override=bob_actor,
        )
