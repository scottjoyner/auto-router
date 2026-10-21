"""Adversarial synthetic tests; private keys live only in this test process."""
from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_router.fleet_publisher_envelope import (
    InMemoryReplayFence,
    PublisherEnvelopeError,
    SignedFleetPublisherVerifier,
    TrustedPublisher,
    signing_message,
)

NOW = 1792585000000
MODEL = "joyner-model-catalog"
PRESENCE = "joyner-gpu-heartbeat"


@pytest.fixture
def signing_keys():
    return {name: Ed25519PrivateKey.generate() for name in (MODEL, PRESENCE)}


@pytest.fixture
def roots(signing_keys):
    return {
        MODEL: TrustedPublisher(
            MODEL, "joyner", "model_catalog", "joyner-catalog-key-v1",
            signing_keys[MODEL].public_key(), ("joyner-toolcall", "joyner-ling"),
        ),
        PRESENCE: TrustedPublisher(
            PRESENCE, "joyner", "gpu_presence", "joyner-presence-key-v1",
            signing_keys[PRESENCE].public_key(),
        ),
    }


def sign(keys, *, publisher=MODEL, sequence=1, issue=NOW, expiration=NOW+30000,
         payload=None, node="joyner", role=None, kid=None, signer=None, schema=None):
    presence = publisher == PRESENCE
    row = {
        "schema": schema or "fleet-publisher-envelope.v1",
        "publisher_id": publisher,
        "node_id": node,
        "role": role or ("gpu_presence" if presence else "model_catalog"),
        "key_id": kid or ("joyner-presence-key-v1" if presence else "joyner-catalog-key-v1"),
        "sequence": sequence,
        "issued_at_ms": issue,
        "expires_at_ms": expiration,
        "payload": payload if payload is not None else (
            {"gpu_canary_guarded": False, "dispatch_admitted": False}
            if presence else
            {"library": ["joyner-ling", "joyner-toolcall"], "loaded": []}
        ),
        "signature": "0" * 86,
    }
    signed = keys[signer or publisher].sign(signing_message(row))
    row["signature"] = base64.urlsafe_b64encode(signed).rstrip(b"=").decode("ascii")
    return row


def rejects(verifier, doc):
    with pytest.raises(PublisherEnvelopeError):
        verifier.verify(doc, now_ms=NOW)


def test_catalog_publisher_signed_identity_is_not_model_residency(signing_keys, roots):
    result = SignedFleetPublisherVerifier(roots).verify(
        sign(signing_keys), now_ms=NOW,
    )
    assert result.signed_publisher_identity_verified
    assert result.role == "model_catalog"
    assert result.production_admission == "NO_GO"
    assert not result.publisher_custody_verified
    assert not result.replay_protection_durable
    assert not result.loaded_model_attested
    assert not result.gpu_residency_verified
    assert not result.subject_owner_bound


def test_independent_signers_and_counter_domains(signing_keys, roots):
    v = SignedFleetPublisherVerifier(roots)
    a = v.verify(sign(signing_keys, publisher=MODEL, sequence=1), now_ms=NOW)
    b = v.verify(sign(signing_keys, publisher=PRESENCE, sequence=1), now_ms=NOW)
    assert a.role == "model_catalog"
    assert b.role == "gpu_presence"
    assert b.production_admission == "NO_GO"


def test_no_trust_roots_is_fail_closed(signing_keys):
    rejects(SignedFleetPublisherVerifier({}), sign(signing_keys))


def test_unknown_key_id_is_rejected(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, kid="retired-key-v0"))


def test_role_confusion_rejected_even_if_signed(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, role="gpu_presence"))


def test_node_spoof_rejected(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, node="xwing"))


def test_wrong_private_key_does_not_authorize_model_report(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, signer=PRESENCE))


def test_valid_signature_cannot_be_reused_for_mutated_catalog(signing_keys, roots):
    row = sign(signing_keys)
    row["payload"]["library"].append("joyner-other")
    rejects(SignedFleetPublisherVerifier(roots), row)


def test_valid_signature_cannot_be_reused_for_mutated_expiry(signing_keys, roots):
    row = sign(signing_keys)
    row["expires_at_ms"] = NOW + 100000
    rejects(SignedFleetPublisherVerifier(roots), row)


def test_replay_same_sequence_rejected(signing_keys, roots):
    v = SignedFleetPublisherVerifier(roots)
    d = sign(signing_keys)
    v.verify(d, now_ms=NOW)
    rejects(v, d)


def test_even_resigned_equal_sequence_rejected(signing_keys, roots):
    v = SignedFleetPublisherVerifier(roots)
    v.verify(sign(signing_keys, sequence=9), now_ms=NOW)
    rejects(v, sign(signing_keys, sequence=9, payload={
        "library": ["joyner-toolcall"], "loaded": [],
    }))


def test_old_sequence_rejected_after_success(signing_keys, roots):
    v = SignedFleetPublisherVerifier(roots)
    v.verify(sign(signing_keys, sequence=9), now_ms=NOW)
    rejects(v, sign(signing_keys, sequence=8))


def test_invalid_signature_does_not_advance_counter(signing_keys, roots):
    v = SignedFleetPublisherVerifier(roots)
    rejects(v, sign(signing_keys, sequence=1, signer=PRESENCE))
    assert v.verify(sign(signing_keys, sequence=1), now_ms=NOW).sequence == 1


def test_expired_document_rejected(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, issue=NOW-30000, expiration=NOW-1))


def test_too_long_lease_rejected(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, expiration=NOW+121000))


def test_future_document_rejected(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, issue=NOW+15001, expiration=NOW+45001))


@pytest.mark.parametrize("bad", [True, False, 0, -1, 2**65, "1", 1.0])
def test_invalid_sequences_rejected(signing_keys, roots, bad):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, sequence=bad))


def test_catalog_cannot_claim_loaded_model(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, payload={
                "library": ["joyner-toolcall"], "loaded": ["joyner-toolcall"],
            }))


def test_catalog_cannot_list_unknown_model(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, payload={"library": ["my-secret-model"], "loaded": []}))


def test_catalog_cannot_provide_model_file_paths(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, payload={
                "library": ["/home/joyner/.config/private.gguf"], "loaded": [],
            }))


def test_catalog_cannot_include_unexpected_fields(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, payload={
                "library": [], "loaded": [], "dispatch_admitted": True,
            }))


def test_presence_cannot_admit_dispatch(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, publisher=PRESENCE, payload={
                "gpu_canary_guarded": True, "dispatch_admitted": True,
            }))


def test_presence_cannot_add_model_catalog(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, publisher=PRESENCE, payload={
                "gpu_canary_guarded": True,
                "dispatch_admitted": False, "library": [],
            }))


def test_presence_rejects_nonbool_guard(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, publisher=PRESENCE, payload={
                "gpu_canary_guarded": 0, "dispatch_admitted": False,
            }))


def test_duplicate_aliases_rejected(signing_keys, roots):
    rejects(SignedFleetPublisherVerifier(roots),
            sign(signing_keys, payload={
                "library": ["joyner-ling", "joyner-ling"], "loaded": [],
            }))


def test_unknown_top_level_fields_rejected_before_signature(signing_keys, roots):
    d = sign(signing_keys)
    d["untrusted_claim"] = "loaded"
    rejects(SignedFleetPublisherVerifier(roots), d)


def test_invalid_base64_rejected(signing_keys, roots):
    d = sign(signing_keys)
    d["signature"] = "!" * 86
    rejects(SignedFleetPublisherVerifier(roots), d)


def test_signature_key_reuse_across_producers_rejected(signing_keys, roots):
    other = TrustedPublisher(
        "second-source", "joyner", "gpu_presence", "joyner-catalog-key-v1",
        signing_keys[PRESENCE].public_key(),
    )
    with pytest.raises(ValueError, match="key IDs"):
        SignedFleetPublisherVerifier({**roots, "second-source": other})


def test_untrusted_root_identity_mismatch_rejected(signing_keys, roots):
    with pytest.raises(ValueError, match="identity mismatch"):
        SignedFleetPublisherVerifier({"wrong": roots[MODEL]})


def test_bad_wire_values_do_not_emit_source_content(signing_keys, roots):
    d = sign(signing_keys)
    d["payload"] = {"library": ["SECRET_PASSWORD"], "loaded": []}
    with pytest.raises(PublisherEnvelopeError) as exc:
        SignedFleetPublisherVerifier(roots).verify(d, now_ms=NOW)
    assert "SECRET_PASSWORD" not in str(exc.value)


def test_inmemory_replay_fence_is_not_durable(signing_keys, roots):
    first = SignedFleetPublisherVerifier(roots)
    first.verify(sign(signing_keys, sequence=1), now_ms=NOW)
    later = SignedFleetPublisherVerifier(roots)
    second = later.verify(sign(signing_keys, sequence=1), now_ms=NOW)
    assert second.replay_protection_durable is False


def test_replay_fence_scopes_producers_independently():
    f = InMemoryReplayFence()
    f.consume(MODEL, 11)
    f.consume(PRESENCE, 1)
    with pytest.raises(PublisherEnvelopeError, match="replay"):
        f.consume(MODEL, 10)


def test_shaped_model_signing_message_is_domain_separated(signing_keys):
    row = sign(signing_keys)
    assert signing_message(row).startswith(b"auto-router:fleet-publisher:v1\x00")


def test_oversized_payload_rejected(signing_keys, roots):
    row = sign(signing_keys)
    row["payload"] = {"library": [], "loaded": [], "untrusted": "x" * 18000}
    rejects(SignedFleetPublisherVerifier(roots), row)


def test_unsigned_legacy_fleet_report_is_rejected(signing_keys, roots):
    legacy = {
        "hostname": "joyner",
        "library": [{"id": "joyner-toolcall", "path": ""}],
        "loaded": [],
        "power_profile": "daily",
    }
    rejects(SignedFleetPublisherVerifier(roots), legacy)


def test_unsigned_legacy_gpu_heartbeat_is_rejected(signing_keys, roots):
    legacy = {
        "hostname": "joyner",
        "library": [],
        "loaded": [],
        "capabilities": [],
        "health": {
            "dispatch_admitted": False,
            "toolcall_3b_unadmitted": True,
        },
    }
    rejects(SignedFleetPublisherVerifier(roots), legacy)


def test_sequential_counter_strictly_advances_and_remains_no_go(signing_keys, roots):
    v = SignedFleetPublisherVerifier(roots)
    for n in (1, 2, 3):
        receipt = v.verify(sign(signing_keys, sequence=n), now_ms=NOW)
        assert receipt.sequence == n
        assert receipt.production_admission == "NO_GO"
    rejects(v, sign(signing_keys, sequence=1))


def test_concurrent_duplicate_sequence_one_accepted(signing_keys, roots):
    from concurrent.futures import ThreadPoolExecutor

    v = SignedFleetPublisherVerifier(roots)
    d = sign(signing_keys, sequence=5)

    def attempt(_n):
        try:
            v.verify(dict(d), now_ms=NOW)
            return True
        except PublisherEnvelopeError:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, range(8)))
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 7