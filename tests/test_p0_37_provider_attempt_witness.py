"""P0 #37 detached signed node-observer correlation: adversarial fixture tests.

None of these fixtures is a live provider process; even a valid signed
terminal is not physical execution proof and never permits promotion.
"""
from __future__ import annotations

import json
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_p0_37_request_digest import _delegation, _payload
from test_p0_37_verified_user_delegation import _KID

from auto_router.provider_attempt_witness import (
    DOMAIN,
    ProviderWitnessRejected,
    correlate_provider_observations,
)
from auto_router.user_delegation import verify_user_delegation

_NOW = 1770675200000
_NODE = "x1-370-test-node"
_MODEL_SHA = "a" * 64
_KEY_ID = "node-observer-fixture-1"


def sign_test_observation(event, fixture_private_key):
    """Signing exists ONLY in fixture tests; production verifier only reads."""
    raw = json.dumps(
        event, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    return raw, fixture_private_key.sign(DOMAIN + raw)


@pytest.fixture
def correlated_fixture():
    gateway_key = Ed25519PrivateKey.generate()
    observer = Ed25519PrivateKey.generate()
    body = _payload()
    token = _delegation(gateway_key, body)
    actor = verify_user_delegation(
        token, public_key=gateway_key.public_key(), key_id=_KID,
        model="auto/code", request_path="/v1/chat/completions",
        request_body=body,
    )
    process = {
        "node_id": _NODE,
        "runtime_instance_id": "fixture-process-1",
        "boot_id": str(uuid.uuid4()),
        "pid": 1204,
        "process_start_ticks": 123456789,
        "model_artifact_sha256": _MODEL_SHA,
        "provider_request_id": "disposable-native-req-1",
    }

    def event(stage: str, seq: int, *, changes=None):
        data = {
            "schema": "p0-provider-attempt-observation-v1",
            "issuer": "independent-node-observer-p0",
            "audience": "auto-router-p0-provider-correlation",
            "key_id": _KEY_ID,
            "root_request_id": actor["root_request_id"],
            "attempt_id": actor["attempt_id"],
            "request_path": actor["request_path"],
            "request_sha256": actor["request_sha256"],
            "model_handle": actor["model_handle"],
            **process,
            "stage": stage,
            "terminal": "completed" if stage == "attempt_terminal" else None,
            "observed_at_ms": _NOW - 10000 + seq * 1000,
            "seq": seq,
            "event_id": str(uuid.uuid4()),
        }
        if changes:
            data.update(changes)
        return sign_test_observation(data, observer)

    def evaluate(events, *, actor_override=None, key=None, node=_NODE, now=_NOW):
        return correlate_provider_observations(
            verified_actor=actor if actor_override is None else actor_override,
            signed_observations=events,
            observer_public_key=observer.public_key() if key is None else key,
            observer_key_id=_KEY_ID,
            expected_node_id=node,
            now_ms=now,
        )

    return actor, observer, event, evaluate


def test_no_observations_is_unknown_not_negative_executed(correlated_fixture):
    _actor, _observer, _event, evaluate = correlated_fixture
    verdict = evaluate([])
    assert verdict["observer_correlation"] == "unobserved"
    assert verdict["provider_execution_attested"] is False
    assert verdict["response_delivered"] == "unknown"
    assert verdict["native_attempt_terminal_observed"] is False
    assert verdict["physical_provider_proof"] is False


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled", "incomplete"])
def test_signed_terminal_correlation_is_not_independent_physical_proof(
    correlated_fixture, terminal,
):
    _actor, _observer, event, evaluate = correlated_fixture
    events = [
        event("resident_snapshot", 0),
        event("attempt_received", 1),
        event("attempt_terminal", 2, changes={"terminal": terminal}),
    ]
    verdict = evaluate(events)
    assert verdict["observer_correlation"] == "signed_terminal_correlated"
    assert verdict["native_attempt_terminal_observed"] is True
    assert verdict["provider_execution_attested"] is False
    assert verdict["physical_provider_proof"] is False
    assert verdict["response_delivered"] == "unknown"


def test_native_first_token_claim_never_proves_ttft_without_physical_sensor(
    correlated_fixture,
):
    _actor, _observer, event, evaluate = correlated_fixture
    events = [
        event("resident_snapshot", 0),
        event("attempt_received", 1),
        event("native_first_token", 2),
        event("attempt_terminal", 3),
    ]
    verdict = evaluate(events)
    assert verdict["observer_correlation"] == "signed_terminal_correlated"
    assert verdict["first_token_proven"] is False
    assert verdict["provider_execution_attested"] is False


def test_only_partial_stages_never_promote(correlated_fixture):
    _actor, _observer, event, evaluate = correlated_fixture
    verdict = evaluate([event("resident_snapshot", 0), event("attempt_received", 1)])
    assert verdict["observer_correlation"] == "signed_partial_only"
    assert verdict["native_attempt_terminal_observed"] is False


def test_missing_initial_stage_and_reordered_sequence_rejected(correlated_fixture):
    _actor, _observer, event, evaluate = correlated_fixture
    with pytest.raises(ProviderWitnessRejected, match="lifecycle"):
        evaluate([event("attempt_received", 0)])
    with pytest.raises(ProviderWitnessRejected, match="lifecycle"):
        evaluate([event("resident_snapshot", 0), event("attempt_terminal", 1)])
    with pytest.raises(ProviderWitnessRejected, match="order"):
        evaluate([event("resident_snapshot", 0), event("attempt_received", 9)])


@pytest.mark.parametrize("field,value", [
    ("root_request_id", str(uuid.uuid4())),
    ("attempt_id", str(uuid.uuid4())),
    ("request_sha256", "b" * 64),
    ("request_path", "/v1/embeddings"),
    ("model_handle", "auto/other"),
])
def test_signed_but_wrong_attempt_or_prompt_cannot_correlate(
    correlated_fixture, field, value,
):
    _actor, _observer, event, evaluate = correlated_fixture
    with pytest.raises(ProviderWitnessRejected, match="verified attempt"):
        evaluate([event("resident_snapshot", 0, changes={field: value})])


@pytest.mark.parametrize("field,value", [
    ("node_id", "other-node"),
    ("model_artifact_sha256", "short"),
    ("boot_id", "fake-boot"),
    ("process_start_ticks", -1),
    ("pid", True),
    ("runtime_instance_id", ""),
    ("provider_request_id", ""),
    ("observed_at_ms", _NOW + 8000),
])
def test_wrong_node_process_or_freshness_cannot_be_inferred(
    correlated_fixture, field, value,
):
    _actor, _observer, event, evaluate = correlated_fixture
    expected = "node identity mismatch" if field == "node_id" else (
        "identity" if field in {"runtime_instance_id", "provider_request_id"}
        else "process identity, order or freshness"
    )
    with pytest.raises(ProviderWitnessRejected, match=expected):
        evaluate([event("resident_snapshot", 0, changes={field: value})])


@pytest.mark.parametrize("field,value", [
    ("pid", 1009),
    ("boot_id", str(uuid.uuid4())),
    ("model_artifact_sha256", "c" * 64),
    ("runtime_instance_id", "different-instance"),
    ("provider_request_id", "different-request"),
])
def test_process_model_or_native_request_switch_in_mid_attempt_fails(
    correlated_fixture, field, value,
):
    _actor, _observer, event, evaluate = correlated_fixture
    with pytest.raises(ProviderWitnessRejected, match="process or provider request changed"):
        evaluate([
            event("resident_snapshot", 0),
            event("attempt_received", 1, changes={field: value}),
        ])


def test_replayed_event_forged_observer_and_wrong_domain_rejected(
    correlated_fixture,
):
    _actor, observer, event, evaluate = correlated_fixture
    first = event("resident_snapshot", 0)
    replay_body, replay_signature = first
    # Repeated event at another sequence is invalid even if re-signed.
    record = json.loads(replay_body)
    record["seq"] = 1
    record["stage"] = "attempt_received"
    reused_nonce = sign_test_observation(record, observer)
    with pytest.raises(ProviderWitnessRejected, match="duplicate observer event"):
        evaluate([first, reused_nonce])

    with pytest.raises(ProviderWitnessRejected, match="untrusted node observer"):
        evaluate([first], key=Ed25519PrivateKey.generate().public_key())

    forged = (replay_body, observer.sign(replay_body))
    with pytest.raises(ProviderWitnessRejected, match="untrusted node observer"):
        evaluate([forged])
    assert DOMAIN not in replay_body


def test_duplicate_json_and_noncanonical_signed_bytes_are_rejected(
    correlated_fixture,
):
    _actor, observer, event, evaluate = correlated_fixture
    body, _signature = event("resident_snapshot", 0)
    duplicate = body[:-1] + b',"seq":0}'
    malformed = (duplicate, observer.sign(DOMAIN + duplicate))
    with pytest.raises(ProviderWitnessRejected, match="duplicate signed observer"):
        evaluate([malformed])
    with pytest.raises(ProviderWitnessRejected, match="noncanonical"):
        spaced = b" " + body
        evaluate([(spaced, observer.sign(DOMAIN + spaced))])


def test_unsigned_caller_metadata_cannot_substitute_internal_verified_v2_actor(
    correlated_fixture,
):
    actor, _observer, event, evaluate = correlated_fixture
    with pytest.raises(ProviderWitnessRejected, match="v2 verified request"):
        evaluate(
            [event("resident_snapshot", 0)],
            actor_override={
                **actor, "issuer": "metadata-from-client",
                "tenant": "forged",
            },
        )
    with pytest.raises(ProviderWitnessRejected, match="v2 verified request"):
        evaluate(
            [event("resident_snapshot", 0)],
            actor_override={
                **actor, "request_sha256": "client-controlled",
            },
        )


def test_missing_terminal_or_stream_done_is_not_physical_execution(
    correlated_fixture,
):
    _actor, _observer, event, evaluate = correlated_fixture
    events = [event("resident_snapshot", 0), event("attempt_received", 1)]
    verdict = evaluate(events)
    assert verdict["native_attempt_terminal_observed"] is False
    assert verdict["first_token_proven"] is False
    assert verdict["provider_execution_attested"] is False


def test_terminal_cannot_attach_to_nonterminal_or_unknown_outcome(
    correlated_fixture,
):
    _actor, _observer, event, evaluate = correlated_fixture
    with pytest.raises(ProviderWitnessRejected, match="terminal state"):
        evaluate([
            event("resident_snapshot", 0, changes={"terminal": "completed"}),
        ])
    with pytest.raises(ProviderWitnessRejected, match="native terminal"):
        evaluate([
            event("resident_snapshot", 0),
            event("attempt_received", 1),
            event("attempt_terminal", 2, changes={"terminal": "invented"}),
        ])
