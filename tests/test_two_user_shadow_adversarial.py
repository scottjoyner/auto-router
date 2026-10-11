"""Adversarial, fixture-only gates for issue #37. No production side effects."""
import pytest
from test_two_user_shadow_receipts import _FixtureGateway, _Principal, _ShadowReceipts
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _session(gateway, principal):
    key = Ed25519PrivateKey.generate()
    gateway.register(principal, key.public_key())
    nonce = gateway.challenge(principal)
    return gateway.login(principal, nonce, key.sign(gateway.proof_message(principal, nonce)))


def test_expired_delegation_replay_fails_without_changing_existing_receipt():
    clock = [1000.0]
    gateway = _FixtureGateway(clock=lambda: clock[0])
    session = _session(gateway, _Principal("shared", "alice"))
    ledger = _ShadowReceipts(gateway)
    delegation = gateway.delegate(session, "auto/local")
    original = ledger.append(delegation, [b"data: [DONE]\n\n"])
    clock[0] += 61
    with pytest.raises(PermissionError, match="expired"):
        ledger.append(delegation, [b"data: [DONE]\n\n"])
    assert ledger.read(session) == [original]


def test_forged_metadata_cannot_promote_protocol_completion_to_execution():
    gateway = _FixtureGateway()
    session = _session(gateway, _Principal("shared", "alice"))
    row = _ShadowReceipts(gateway).append(
        gateway.delegate(session, "auto/local"),
        [b"data: [DONE]\n\n"],
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


def test_same_trace_collision_preserves_owner_bound_retry_and_read_isolation():
    gateway = _FixtureGateway()
    a = _session(gateway, _Principal("shared", "alice"))
    b = _session(gateway, _Principal("shared", "bob"))
    ledger = _ShadowReceipts(gateway)
    first = ledger.append(gateway.delegate(a, "auto/local"),
        [b"data: [DONE]\n\n"], client_metadata={"trace_id": "collision"})
    other = ledger.append(gateway.delegate(b, "auto/local"),
        [b"data: [DONE]\n\n"], client_metadata={"trace_id": "collision"})
    retry = ledger.append(gateway.delegate(a, "auto/local", retry_root=first["root_request_id"]),
        [b'data: {"choices":[]}\n\n'], client_metadata={"trace_id": "collision"})
    assert first["client_trace_fingerprint"] == other["client_trace_fingerprint"]
    assert retry["root_request_id"] == first["root_request_id"]
    assert retry["attempt_id"] != first["attempt_id"]
    assert ledger.read(b, root_request_id=first["root_request_id"]) == []
    assert ledger.read(a, root_request_id=other["root_request_id"]) == []
