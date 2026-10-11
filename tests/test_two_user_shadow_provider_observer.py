"""Issue #37: independent *synthetic* provider-observer correlation contract.

Two distinct fixture Ed25519 keys are used: one for user-to-gateway delegation
and one for a simulated process observer. The signed observer envelope is not
physical LM Studio evidence and MUST NEVER set provider_execution_attested.
No network requests, providers, spool, production routing or custody are used.
"""
from __future__ import annotations

import json
import uuid

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_two_user_shadow_receipts import _FixtureGateway, _Principal, _ShadowReceipts

_STAGES = ("resident", "attempt_started", "attempt_terminal")
_NODE = "configured-test-node"
_MODEL = "auto/local"


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _login(gateway, name):
    principal = _Principal("same-tenant", name)
    private_key = Ed25519PrivateKey.generate()
    gateway.register(principal, private_key.public_key())
    nonce = gateway.challenge(principal)
    return gateway.login(
        principal,
        nonce,
        private_key.sign(gateway.proof_message(principal, nonce)),
    )


class _SyntheticProcessObserver:
    """Separate fixture signing authority, NOT a real process observer."""

    def __init__(self, gateway, *, clock=None):
        self.gateway = gateway
        self.clock = clock or gateway.clock
        self.signer = Ed25519PrivateKey.generate()

    def event(
        self, delegation, stage, *,
        instance="disposable-fixture-process-1",
        node=_NODE,
        model=_MODEL,
        timestamp=None,
    ):
        bound = self.gateway.verify(delegation)
        if stage not in _STAGES:
            raise ValueError("unknown synthetic witness stage")
        payload = {
            "schema": "shadow-provider-observer-v1",
            "issuer": "disposable-provider-observer-only",
            "audience": "shadow-correlation-only",
            "root_request_id": bound["root_request_id"],
            "attempt_id": bound["attempt_id"],
            "model_handle": model,
            "node_id": node,
            "runtime_instance_id": instance,
            "stage": stage,
            "event_id": uuid.uuid4().hex,
            "observed_at": int(self.clock()) if timestamp is None else timestamp,
        }
        body = _canonical(payload)
        return body, self.signer.sign(body)

    def sequence(self, delegation):
        return [self.event(delegation, stage) for stage in _STAGES]


class _FixtureCorrelator:
    """Fail-closed evidence comparator. It NEVER issues real execution claims."""

    def __init__(self, gateway, observer_public_key, *, clock=None):
        self.gateway = gateway
        self.observer_public_key = observer_public_key
        self.clock = clock or gateway.clock

    def compare(self, delegation, receipt, observations):
        bound = self.gateway.verify(delegation)
        for field in ("root_request_id", "attempt_id", "model_handle"):
            if receipt.get(field) != bound[field]:
                raise PermissionError("receipt/delegation identity mismatch")
        if (receipt.get("tenant_id") != bound["tenant"]
                or receipt.get("subject_id") != bound["subject"]):
            raise PermissionError("receipt owner mismatch")
        if receipt.get("node_id") != _NODE:
            raise PermissionError("untrusted configured node identity")
        if (receipt.get("provider_execution_attested") is not False
                or receipt.get("response_delivered") != "unknown"):
            raise PermissionError("untrusted execution/delivery claim")

        outcome = {
            "fixture_observer_correlation": "unobserved",
            "provider_execution_attested": False,
            "response_delivered": "unknown",
            "terminal_status": receipt["terminal_status"],
            "physical_provider_proof": False,
        }
        if not observations:
            return outcome
        if len(observations) != len(_STAGES):
            return outcome

        decoded = []
        seen_ids = set()
        for body, signature in observations:
            if not isinstance(body, bytes) or len(body) > 1024:
                raise PermissionError("invalid observer envelope")
            if not isinstance(signature, bytes) or len(signature) != 64:
                raise PermissionError("invalid observer signature")
            try:
                self.observer_public_key.verify(signature, body)
                event = json.loads(body)
            except (InvalidSignature, ValueError, UnicodeDecodeError, TypeError) as exc:
                raise PermissionError("untrusted provider observer") from exc
            if not isinstance(event, dict) or set(event) != {
                "schema", "issuer", "audience", "root_request_id", "attempt_id",
                "model_handle", "node_id", "runtime_instance_id", "stage",
                "event_id", "observed_at",
            }:
                raise PermissionError("invalid observer event schema")
            if (event["schema"] != "shadow-provider-observer-v1"
                    or event["issuer"] != "disposable-provider-observer-only"
                    or event["audience"] != "shadow-correlation-only"):
                raise PermissionError("invalid observer audience")
            for field in ("root_request_id", "attempt_id", "model_handle"):
                if event[field] != bound[field]:
                    raise PermissionError("provider/attempt mismatch")
            if event["node_id"] != _NODE:
                raise PermissionError("provider/node mismatch")
            if (not isinstance(event["runtime_instance_id"], str)
                    or not 0 < len(event["runtime_instance_id"]) <= 128):
                raise PermissionError("invalid observer instance")
            if (not isinstance(event["event_id"], str)
                    or len(event["event_id"]) != 32
                    or event["event_id"] in seen_ids):
                raise PermissionError("duplicated or invalid observer event")
            seen_ids.add(event["event_id"])
            seen_at = event["observed_at"]
            if (type(seen_at) is not int
                    or seen_at < bound["iat"] - 5
                    or seen_at > int(self.clock()) + 1):
                raise PermissionError("stale or future observer event")
            decoded.append(event)

        if tuple(event["stage"] for event in decoded) != _STAGES:
            return outcome
        if len({event["runtime_instance_id"] for event in decoded}) != 1:
            raise PermissionError("process instance changed mid-attempt")
        if len({event["model_handle"] for event in decoded}) != 1:
            raise PermissionError("provider model mismatch")
        timestamps = [event["observed_at"] for event in decoded]
        if timestamps != sorted(timestamps):
            raise PermissionError("out-of-order provider time")
        outcome["fixture_observer_correlation"] = "synthetic_matched_sequence"
        return outcome


def _build(gateway, user, *, client_metadata=None, chunks=None):
    token = gateway.delegate(user, _MODEL)
    receipt = _ShadowReceipts(gateway).append(
        token,
        chunks if chunks is not None else [b"data: [DONE]\n\n"],
        client_metadata=client_metadata,
    )
    return token, receipt


def test_signed_observer_sequence_never_promotes_synthetic_stream_to_real_execution():
    gateway = _FixtureGateway()
    alice = _login(gateway, "alice")
    token, receipt = _build(
        gateway,
        alice,
        client_metadata={
            "provider_execution_attested": True,
            "runtime_instance_id": "spoofed-instance",
        },
    )
    observer = _SyntheticProcessObserver(gateway)
    outcome = _FixtureCorrelator(
        gateway, observer.signer.public_key()
    ).compare(token, receipt, observer.sequence(token))
    assert outcome["fixture_observer_correlation"] == "synthetic_matched_sequence"
    assert outcome["provider_execution_attested"] is False
    assert outcome["physical_provider_proof"] is False
    assert outcome["response_delivered"] == "unknown"
    assert "spoofed-instance" not in str(outcome)


def test_completed_stream_without_observer_does_not_prove_execution():
    gateway = _FixtureGateway()
    alice = _login(gateway, "alice")
    token, receipt = _build(gateway, alice)
    observer = _SyntheticProcessObserver(gateway)
    outcome = _FixtureCorrelator(gateway, observer.signer.public_key()).compare(
        token, receipt, []
    )
    assert receipt["terminal_status"] == "completed"
    assert outcome["fixture_observer_correlation"] == "unobserved"
    assert outcome["provider_execution_attested"] is False


def test_two_owners_same_model_same_client_trace_still_have_separate_observation():
    gateway = _FixtureGateway()
    alice, bob = _login(gateway, "alice"), _login(gateway, "bob")
    observer = _SyntheticProcessObserver(gateway)
    correlator = _FixtureCorrelator(gateway, observer.signer.public_key())
    token_a, row_a = _build(gateway, alice, client_metadata={"trace_id": "collision"})
    token_b, row_b = _build(gateway, bob, client_metadata={"trace_id": "collision"})
    assert row_a["client_trace_fingerprint"] == row_b["client_trace_fingerprint"]
    assert row_a["attempt_id"] != row_b["attempt_id"]
    assert correlator.compare(
        token_a, row_a, observer.sequence(token_a)
    )["fixture_observer_correlation"] == "synthetic_matched_sequence"
    assert correlator.compare(
        token_b, row_b, observer.sequence(token_b)
    )["fixture_observer_correlation"] == "synthetic_matched_sequence"
    with pytest.raises(PermissionError, match="provider/attempt mismatch"):
        correlator.compare(token_a, row_a, observer.sequence(token_b))


def test_wrong_signing_key_and_tampered_observer_envelope_fail_closed():
    gateway = _FixtureGateway()
    alice = _login(gateway, "alice")
    token, receipt = _build(gateway, alice)
    observer = _SyntheticProcessObserver(gateway)
    events = observer.sequence(token)
    wrong_key = Ed25519PrivateKey.generate().public_key()
    with pytest.raises(PermissionError, match="untrusted provider observer"):
        _FixtureCorrelator(gateway, wrong_key).compare(token, receipt, events)
    replaced = list(events)
    body, signature = replaced[0]
    replaced[0] = (body + b" ", signature)
    with pytest.raises(PermissionError, match="untrusted provider observer"):
        _FixtureCorrelator(
            gateway, observer.signer.public_key()
        ).compare(token, receipt, replaced)


@pytest.mark.parametrize("change,error", [
    ({"node": "wrong-node"}, "provider/node mismatch"),
    ({"model": "unauthorized-model"}, "provider/attempt mismatch"),
    ({"instance": ""}, "invalid observer instance"),
])
def test_signed_observer_mismatch_rejected_even_when_it_has_a_valid_signature(
    change, error
):
    gateway = _FixtureGateway()
    alice = _login(gateway, "alice")
    token, receipt = _build(gateway, alice)
    observer = _SyntheticProcessObserver(gateway)
    events = observer.sequence(token)
    events[1] = observer.event(token, _STAGES[1], **change)
    correlator = _FixtureCorrelator(gateway, observer.signer.public_key())
    with pytest.raises(PermissionError, match=error):
        correlator.compare(token, receipt, events)


def test_missing_or_wrong_order_observer_stage_cannot_upgrade_an_incomplete_stream():
    gateway = _FixtureGateway()
    alice = _login(gateway, "alice")
    token, receipt = _build(
        gateway, alice, chunks=[b'data: {"choices":[]}\n\n']
    )
    observer = _SyntheticProcessObserver(gateway)
    correlator = _FixtureCorrelator(gateway, observer.signer.public_key())
    events = observer.sequence(token)
    assert correlator.compare(token, receipt, events[:2])["fixture_observer_correlation"] == "unobserved"
    assert correlator.compare(token, receipt, events[::-1])["fixture_observer_correlation"] == "unobserved"
    correlated = correlator.compare(token, receipt, events)
    assert correlated["fixture_observer_correlation"] == "synthetic_matched_sequence"
    assert correlated["terminal_status"] == "incomplete"
    assert correlated["provider_execution_attested"] is False


def test_duplicate_observer_event_and_inconsistent_instance_fail_closed():
    gateway = _FixtureGateway()
    alice = _login(gateway, "alice")
    token, receipt = _build(gateway, alice)
    observer = _SyntheticProcessObserver(gateway)
    correlator = _FixtureCorrelator(gateway, observer.signer.public_key())
    events = observer.sequence(token)
    with pytest.raises(PermissionError, match="duplicated"):
        correlator.compare(token, receipt, [events[0], events[0], events[2]])
    different_instance = list(events)
    different_instance[2] = observer.event(
        token, "attempt_terminal", instance="other-process"
    )
    with pytest.raises(PermissionError, match="process instance changed"):
        correlator.compare(token, receipt, different_instance)


def test_signed_stale_provider_observation_is_rejected_with_a_valid_delegation():
    clock = [2000.0]
    gateway = _FixtureGateway(clock=lambda: clock[0])
    alice = _login(gateway, "alice")
    token, receipt = _build(gateway, alice)
    observer = _SyntheticProcessObserver(gateway)
    correlator = _FixtureCorrelator(gateway, observer.signer.public_key())
    events = observer.sequence(token)
    events[1] = observer.event(token, "attempt_started", timestamp=1900)
    with pytest.raises(PermissionError, match="stale"):
        correlator.compare(token, receipt, events)
