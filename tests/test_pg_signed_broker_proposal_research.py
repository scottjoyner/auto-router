"""Synthetic two-root signed proposal tests. No DB, network or live grant."""
import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from research.fenced_authority.broker_signed_proposal import (
    ResearchDeny, canonical, propose_only,
)

NOW = 1792885000


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_jwt(private, payload):
    header = {"alg": "EdDSA", "kid": "assistx-executor-v1",
              "typ": "assistx-executor+jwt"}
    unsigned = b64(canonical(header))+"."+b64(canonical(payload))
    return unsigned+"."+b64(private.sign(unsigned.encode("ascii")))


def sign_witness(private, payload):
    return {"payload": payload,
            "signer_id": "resource-witness-v1",
            "signature": b64(private.sign(canonical(payload)))}


@pytest.fixture
def fixture():
    issuer = Ed25519PrivateKey.generate()
    resource = Ed25519PrivateKey.generate()
    claims = {
        "iss": "assistx", "aud": ["auto-router"], "scopes": ["inference"],
        "task_id": "task-fixture", "claim_id": "claim-fixture",
        "agent_id": "agent-fixture", "jti": "unique-fixture-jti",
        "owner_issuer": "test-idp", "owner_subject": "test-owner-a",
        "iat": NOW-2, "nbf": NOW-2, "exp": NOW+18,
        "projection_generation": 4, "max_attempts": 2,
        "max_input_tokens": 40, "max_output_tokens": 20,
        "allowed_model_aliases": ["local/test-model"],
    }
    token = sign_jwt(issuer, claims)
    witness = {
        "issuer": "resource-witness", "aud": "fence-research-only",
        "state": "active", "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "task_id": claims["task_id"], "claim_id": claims["claim_id"],
        "owner_issuer": claims["owner_issuer"],
        "owner_subject": claims["owner_subject"],
        "jti": claims["jti"], "model": "local/test-model",
        "account_scope": "synthetic-account", "projection_generation": 4,
        "max_attempts": 2, "request_id": "synthetic-request",
        "epoch": 4, "issued_at": NOW-1, "expires_at": NOW+10,
        "revocation_generation": 7, "runtime_instance_id": "synthetic-runtime",
    }
    return issuer, resource, claims, witness, token


def run(f, *, token=None, witness=None, issuer=None, resource=None,
        model="local/test-model", account="synthetic-account",
        request_id="synthetic-request", now=NOW):
    a,b,claims,wp,original = f
    return propose_only(
        token=token if token is not None else original,
        resource_witness=witness if witness is not None else sign_witness(b,wp),
        issuer_public_key=(issuer or a).public_key(),
        resource_public_key=(resource or b).public_key(),
        requested_model=model, account_scope=account, request_id=request_id,
        now=now)


def test_two_distinct_signed_trust_roots_make_nonexecuting_proposal(fixture):
    r=run(fixture)
    assert r["decision"]=="VERIFIED_SIGNED_INPUTS_ONLY_NOT_RESOURCE_ADMISSION"
    assert all(len(r[k])==64 for k in
               ("scope_hash","owner_hash","claim_hash","jti_hash",
                "model_hash","request_hash","runtime_hash"))
    assert r["provider_calls"]==0
    assert r["sql_executed"] is False
    assert r["broker_admission_authorized"] is False
    assert r["provider_execution_authorized"] is False
    assert r["current_revocation_proven"] is False
    assert r["external_monotonic_highwater_proven"] is False


@pytest.mark.parametrize("field,bad", [
    ("iss","evil"),("aud",["other"]),("scopes",["admin"]),
    ("allowed_model_aliases",[]),("allowed_model_aliases",["other-model"]),
    ("allowed_model_aliases",["local/test-model","local/test-model"]),
    ("owner_subject","different-owner"),("claim_id","new-claim"),
    ("projection_generation",0),("max_attempts",101),
    ("iat",NOW+30),("nbf",NOW+1),("exp",NOW),
    ("exp",NOW+120),("max_input_tokens",True),
    ("max_output_tokens",0),
])
def test_signed_claim_mismatch_or_budget_denied(fixture, field, bad):
    a,b,cl,wp,token=fixture
    bad_claims={**cl,field:bad}
    forged=sign_jwt(a,bad_claims)
    with pytest.raises(ResearchDeny):
        run(fixture,token=forged)


@pytest.mark.parametrize("field,bad", [
    ("state","revoked"),("issuer","attacker"),("aud","provider"),
    ("claim_id","stale"),("owner_subject","other-owner"),
    ("owner_issuer","other-idp"),("jti","other-jti"),("model","other-model"),
    ("account_scope","other-account"),("request_id","other-request"),
    ("token_sha256","0"*64),("projection_generation",99),
    ("max_attempts",200),("epoch",0),("revocation_generation",0),
    ("expires_at",NOW),("expires_at",NOW+60),
    ("issued_at",NOW-30),("issued_at",NOW+1),
])
def test_signed_witness_claim_or_freshness_mismatch_denied(fixture,field,bad):
    a,b,cl,wp,token=fixture
    signed=sign_witness(b,{**wp,field:bad})
    with pytest.raises(ResearchDeny):
        run(fixture,witness=signed)


def test_forged_jwt_signature_denied(fixture):
    a,b,cl,wp,token=fixture
    evil=Ed25519PrivateKey.generate()
    with pytest.raises(ResearchDeny,match="signature"):
        run(fixture,token=sign_jwt(evil,cl))


def test_forged_resource_signature_denied(fixture):
    a,b,cl,wp,token=fixture
    evil=Ed25519PrivateKey.generate()
    with pytest.raises(ResearchDeny,match="signature"):
        run(fixture,witness=sign_witness(evil,wp))


def test_same_pinned_root_denied(fixture):
    a,b,cl,wp,token=fixture
    with pytest.raises(ResearchDeny,match="independent trust roots"):
        run(fixture,resource=a)


@pytest.mark.parametrize("field,altered", [
    ("model","external/model"),("account","unapproved-account"),
    ("request_id","replayed-other-request"),("now",NOW+22),
])
def test_caller_cannot_change_signed_request_binding(fixture,field,altered):
    with pytest.raises(ResearchDeny):
        run(fixture,**{field:altered})


def test_unsigned_or_malformed_envelope_denied(fixture):
    a,b,cl,wp,token=fixture
    with pytest.raises(ResearchDeny):
        run(fixture,witness={"payload":wp,"signature":"", "signer_id":"resource-witness-v1"})
    with pytest.raises(ResearchDeny):
        run(fixture,witness={"payload":wp,"signature":"fake", "signer_id":"attacker"})


def test_signed_forged_owner_does_not_match_independent_witness(fixture):
    a,b,cl,wp,token=fixture
    fake=sign_jwt(a,{**cl,"owner_subject":"other-owner"})
    with pytest.raises(ResearchDeny,match="resource witness disagrees"):
        run(fixture,token=fake,witness=sign_witness(b,{**wp,"token_sha256":hashlib.sha256(fake.encode()).hexdigest()}))


def test_source_cannot_dispatch_database_or_provider():
    path=Path(__file__).resolve().parents[1]/"research/fenced_authority/broker_signed_proposal.py"
    s=path.read_text()
    assert "provider_execution_authorized" in s
    assert '"sql_executed": False' in s
    for forbidden in ("requests.post(", "httpx.", "psycopg", "sqlite3.connect(", "subprocess.run(", "docker run", "SELECT fence_research."):
        assert forbidden not in s


def test_recent_signed_witness_replay_not_mistaken_for_current_authority(fixture):
    # Replay inside TTL is cryptographically valid but not a fresh live fence.
    _, resource, _, payload, _ = fixture
    cached = sign_witness(resource, payload)
    first = run(fixture, witness=cached, now=NOW)
    replayed = run(fixture, witness=cached, now=NOW+1)
    assert replayed["request_hash"] == first["request_hash"]
    assert replayed["current_revocation_proven"] is False
    assert replayed["external_monotonic_highwater_proven"] is False
    assert replayed["broker_admission_authorized"] is False


def test_request_bound_to_original_owner_across_distinct_accounts(fixture):
    _, resource, _, signed_payload, _ = fixture
    with pytest.raises(ResearchDeny):
        run(fixture, witness=sign_witness(resource,signed_payload),
            account="different-tenant")


def test_witness_cannot_add_unvetted_extension_fields(fixture):
    _, resource, _, signed_payload, _ = fixture
    payload={**signed_payload,"operator_override":True}
    with pytest.raises(ResearchDeny,match="schema drift"):
        run(fixture,witness=sign_witness(resource,payload))
