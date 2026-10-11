"""Additional isolated P0 shadow-contract regression checks.

Only imports the disposable fixture from PR #42. No live services or custody.
"""
import pytest

from test_two_user_shadow_receipts import _FixtureGateway, _Principal, _ShadowReceipts
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _login(gateway, principal, key):
    gateway.register(principal, key.public_key())
    nonce = gateway.challenge(principal)
    return gateway.login(principal, nonce, key.sign(gateway.proof_message(principal, nonce)))


def test_expired_delegation_cannot_be_replayed_as_fresh_execution():
    clock = [1000.0]
    gateway = _FixtureGateway(clock=lambda: clock[0])
    alice = _Principal("shared", "alice")
    session = _login(gateway, alice, Ed25519PrivateKey.generate())
    ledger = _ShadowReceipts(gateway)
    delegation = gateway.delegate(session, "auto/local")
    original = ledger.append(delegation, [b"data: [DONE]\\n\\n"])
    clock[0] += 61
    with pytest.raises(PermissionError, match="expired"):
        ledger.append(delegation, [b"data: [DONE]\\n\\n"])
    assert ledger.read(session) == [original]


def test_completed_protocol_never_becomes_attested_execution_or_delivery():
    gateway = _FixtureGateway()
    alice = _Principal("shared", "alice")
    session = _login(gateway, alice, Ed25519PrivateKey.generate())
    row = _ShadowReceipts(gateway).append(
        gateway.delegate(session, "auto/local"),
        [b"data: [DONE]\\n\\n"],
        client_metadata={
            "provider_execution_attested": True,
            "response_delivered": True,
            "model_instance_id": "forged-process",
            "subject_id": "bob",
        },
    )
    assert row["terminal_status"] == "completed"
    assert row["provider_execution_attested"] is False
    assert row["response_delivered"] == "unknown"
    assert row["instance_identity_source"] == "configured_route_not_runtime_attestation"
    assert "forged-process" not in str(row)
    assert row["subject_id"] == "alice"


def test_same_trace_collision_does_not_allow_cross_subject_read_after_retry():
    gateway = _FixtureGateway()
    alice, bob = _Principal("shared", "alice"), _Principal("shared", "bob")
    a = _login(gateway, alice, Ed25519PrivateKey.generate())
    b = _login(gateway, bob, Ed25519PrivateKey.generate())
    ledger = _ShadowReceipts(gateway)
    a1 = ledger.append(gateway.delegate(a, "auto/local"),
        [b"data: [DONE]\\n\\n"], client_metadata={"trace_id": "collision"})
    b1 = ledger.append(gateway.delegate(b, "auto/local"),
        [b"data: [DONE]\\n\\n"], client_metadata={"trace_id": "collision"})
    a2 = ledger.append(gateway.delegate(a, "auto/local", retry_root=a1["root_request_id"]),
        [b"data: {\\"choices\\":[]}\\n\\n"], client_metadata={"trace_id": "collision"})
    assert a1["client_trace_fingerprint"] == b1["client_trace_fingerprint"]
    assert a2["root_request_id"] == a1["root_request_id"]
    assert a2["attempt_id"] != a1["attempt_id"]
    assert ledger.read(b, root_request_id=a1["root_request_id"]) == []
    assert ledger.read(a, root_request_id=b1["root_request_id"]) == []
