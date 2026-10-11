"""Opt-in P0 per-human signed-delegation tests against REAL ASGI middleware.

Nothing is installed into a live router. Disposable Ed25519 keys and temporary
SQLite test DBs exercise existing executor authentication and idempotency.
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
import uuid
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auto_router.executor_auth import ExecutorInferenceAuthMiddleware
from auto_router.request_idempotency import (
    RequestIdempotencyLedger,
    RequestIdempotencyMiddleware,
)
from auto_router.user_root_ledger import RootOwnerLedger
from auto_router.user_delegation import (
    UserDelegationShadowMiddleware,
    install_user_delegation_shadow,
    verify_user_delegation,
)

_SERVICE = "fixture-shared-service-token"
_KID = "fixture-human-key-1"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _token(
    signer: Ed25519PrivateKey,
    subject: str = "alice",
    *,
    model: str = "auto/code",
    root: str | None = None,
    attempt: str | None = None,
    claims_changes: dict | None = None,
    header_changes: dict | None = None,
):
    now = int(time.time())
    header = {
        "alg": "EdDSA",
        "typ": "auto-router-user-delegation+jwt",
        "kid": _KID,
    }
    claims = {
        "schema": "p0-user-shadow-v1",
        "iss": "trusted-user-gateway-p0",
        "aud": "auto-router-p0-shadow-user",
        "tenant": "same-tenant",
        "subject": subject,
        "root_request_id": root or str(uuid.uuid4()),
        "attempt_id": attempt or str(uuid.uuid4()),
        "model_handle": model,
        "iat": now,
        "nbf": now,
        "exp": now + 60,
        "jti": str(uuid.uuid4()),
    }
    if claims_changes:
        claims.update(claims_changes)
    if header_changes:
        header.update(header_changes)
    encoded_header = _b64(
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    )
    encoded_claims = _b64(
        json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    )
    message = f"{encoded_header}.{encoded_claims}".encode("ascii")
    return f"{encoded_header}.{encoded_claims}.{_b64(signer.sign(message))}"


def _app(tmp_path, monkeypatch, signer):
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "true")
    monkeypatch.setenv("AUTO_ROUTER_INTERNAL_SERVICE_TOKEN", _SERVICE)
    ledger = RequestIdempotencyLedger(
        f"sqlite:///{tmp_path / 'verified-idempotency.sqlite'}"
    )
    app = FastAPI()
    observed = []

    @app.post("/v1/chat/completions")
    async def completion(request: Request):
        observed.append({
            "actor": request.scope.get("auto_router.p0_verified_user_delegation"),
            "body": await request.json(),
        })
        return {"id": "fixture-completion", "choices": []}

    # Exact strict-live ordering: outer executor auth, signed delegation,
    # inner durable request idempotency, then routing.
    app.add_middleware(RequestIdempotencyMiddleware, ledger=ledger)
    app.add_middleware(
        UserDelegationShadowMiddleware,
        public_key=signer.public_key(),
        key_id=_KID,
        owner_ledger=RootOwnerLedger(
            tmp_path / "p0-owner-test.sqlite", b"x" * 32
        ),
    )
    app.add_middleware(
        ExecutorInferenceAuthMiddleware,
        state=SimpleNamespace(runtime_projection_manager=None),
    )
    return app, observed, ledger


def _request(client, delegation, *, asserted_user="spoofed", idem="same-id"):
    headers = {
        "Authorization": f"Bearer {_SERVICE}",
        "Idempotency-Key": idem,
    }
    if delegation is not None:
        headers["X-Auto-Router-User-Delegation"] = delegation
    return client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "auto/code",
            "max_tokens": 8,
            "user": asserted_user,
            "messages": [{"role": "user", "content": "synthetic"}],
            "metadata": {
                "tenant": "attacker-tenant",
                "subject": asserted_user,
                "root_request_id": "forged-root",
                "provider_execution_attested": True,
            },
        },
    )


def test_two_signed_humans_same_service_bearer_isolated_at_idempotency(tmp_path, monkeypatch):
    signer = Ed25519PrivateKey.generate()
    app, observed, ledger = _app(tmp_path, monkeypatch, signer)
    alice = _token(signer, "alice")
    bob = _token(signer, "bob")
    with TestClient(app) as client:
        a = _request(client, alice, asserted_user="bob")
        b = _request(client, bob, asserted_user="alice")
        a_again = _request(client, alice, asserted_user="bob")
    assert (a.status_code, b.status_code, a_again.status_code) == (200, 200, 409)
    assert len(observed) == 2
    assert [row["actor"]["subject"] for row in observed] == ["alice", "bob"]
    assert observed[0]["actor"]["root_request_id"] != "forged-root"
    assert observed[1]["actor"]["root_request_id"] != "forged-root"
    assert observed[0]["body"]["metadata"]["subject"] == "bob"
    assert observed[1]["body"]["metadata"]["subject"] == "alice"
    assert observed[0]["body"]["metadata"]["idempotency_key"] != (
        observed[1]["body"]["metadata"]["idempotency_key"]
    )
    with sqlite3.connect(ledger.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM inference_request_idempotency"
        ).fetchone()[0] == 2
        columns = {
            row[1] for row in db.execute(
                "PRAGMA table_info(inference_request_idempotency)"
            )
        }
        assert "subject" not in columns and "tenant" not in columns


def test_new_signed_attempt_on_same_root_is_distinct_from_replayed_attempt(
    tmp_path, monkeypatch,
):
    signer = Ed25519PrivateKey.generate()
    app, observed, _ = _app(tmp_path, monkeypatch, signer)
    root = str(uuid.uuid4())
    alice_attempt_1 = _token(signer, "alice", root=root)
    alice_attempt_2 = _token(signer, "alice", root=root)
    with TestClient(app) as client:
        first = _request(client, alice_attempt_1)
        retry = _request(client, alice_attempt_2)
        duplicate = _request(client, alice_attempt_2)
    assert [first.status_code, retry.status_code, duplicate.status_code] == [
        200, 200, 409
    ]
    assert len(observed) == 2
    assert observed[0]["actor"]["root_request_id"] == root
    assert observed[1]["actor"]["root_request_id"] == root
    assert observed[0]["actor"]["attempt_id"] != observed[1]["actor"]["attempt_id"]


def test_missing_delegation_keeps_legacy_shared_service_namespace(
    tmp_path, monkeypatch,
):
    signer = Ed25519PrivateKey.generate()
    app, observed, _ = _app(tmp_path, monkeypatch, signer)
    with TestClient(app) as client:
        first = _request(client, None, asserted_user="alice")
        second = _request(client, None, asserted_user="bob")
    assert first.status_code == 200
    assert second.status_code == 409
    assert len(observed) == 1
    assert observed[0]["actor"] is None


@pytest.mark.parametrize("modifier", [
    lambda token, signer: token[:-1] + ("A" if token[-1] != "A" else "B"),
    lambda token, signer: _token(
        Ed25519PrivateKey.generate(), "alice"
    ),
    lambda token, signer: _token(signer, "alice", model="auto/other"),
    lambda token, signer: _token(
        signer, "alice", header_changes={"kid": "unknown-key"}
    ),
    lambda token, signer: _token(
        signer, "alice", claims_changes={"aud": "wrong-audience"}
    ),
    lambda token, signer: _token(
        signer, "alice", claims_changes={"exp": int(time.time()) - 1}
    ),
    lambda token, signer: _token(
        signer, "alice", claims_changes={"subject": "space in name"}
    ),
    lambda token, signer: _token(
        signer, "alice", claims_changes={"attempt_id": "attacker"}
    ),
])
def test_untrusted_delegations_denied_before_idempotency_or_routing(
    tmp_path, monkeypatch, modifier,
):
    signer = Ed25519PrivateKey.generate()
    app, observed, ledger = _app(tmp_path, monkeypatch, signer)
    token = modifier(_token(signer), signer)
    with TestClient(app) as client:
        response = _request(client, token)
    assert response.status_code == 401
    assert observed == []
    with sqlite3.connect(ledger.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM inference_request_idempotency"
        ).fetchone()[0] == 0


def test_delegation_denied_with_wrong_existing_service_auth(
    tmp_path, monkeypatch,
):
    signer = Ed25519PrivateKey.generate()
    app, observed, ledger = _app(tmp_path, monkeypatch, signer)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": "Bearer unknown-other-service-token",
                "X-Auto-Router-User-Delegation": _token(signer),
            },
            json={"model": "auto/code", "max_tokens": 8},
        )
    assert response.status_code == 401
    assert observed == []
    assert ledger.get("anything") is None


def test_ambiguous_duplicate_delegation_header_denied(tmp_path, monkeypatch):
    signer = Ed25519PrivateKey.generate()
    app, observed, _ = _app(tmp_path, monkeypatch, signer)
    token = _token(signer)
    with TestClient(app) as client:
        result = client.post(
            "/v1/chat/completions",
            headers=[
                ("Authorization", f"Bearer {_SERVICE}"),
                ("X-Auto-Router-User-Delegation", token),
                ("X-Auto-Router-User-Delegation", token),
            ],
            json={"model": "auto/code", "max_tokens": 8},
        )
    assert result.status_code == 401
    assert observed == []


def test_installer_disabled_unless_opted_in_and_fails_missing_key(
    monkeypatch, tmp_path,
):
    app = FastAPI()
    monkeypatch.delenv("AUTO_ROUTER_P0_USER_DELEGATION_SHADOW_ENABLED", raising=False)
    install_user_delegation_shadow(app)
    assert not getattr(app.state, "p0_user_delegation_shadow_installed", False)

    monkeypatch.setenv("AUTO_ROUTER_P0_USER_DELEGATION_SHADOW_ENABLED", "true")
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "true")
    monkeypatch.setenv("AUTO_ROUTER_INTERNAL_SERVICE_TOKEN", _SERVICE)
    monkeypatch.delenv("AUTO_ROUTER_P0_USER_DELEGATION_VERIFY_KEY_FILE", raising=False)
    with pytest.raises(RuntimeError, match="pinned"):
        install_user_delegation_shadow(app)
    signer = Ed25519PrivateKey.generate()
    key_path = tmp_path / "public.pem"
    key_path.write_bytes(signer.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    monkeypatch.setenv("AUTO_ROUTER_P0_USER_DELEGATION_VERIFY_KEY_FILE", str(key_path))
    monkeypatch.setenv("AUTO_ROUTER_P0_USER_DELEGATION_KEY_ID", _KID)
    secret_path = tmp_path / "ledger-key.bin"
    secret_path.write_bytes(os.urandom(32))
    secret_path.chmod(0o600)
    monkeypatch.setenv("AUTO_ROUTER_P0_ROOT_OWNER_HMAC_KEY_FILE", str(secret_path))
    monkeypatch.setenv(
        "AUTO_ROUTER_P0_ROOT_OWNER_LEDGER_FILE",
        str(tmp_path / "owner-ledger.sqlite"),
    )
    install_user_delegation_shadow(app)
    assert app.state.p0_user_delegation_shadow_installed is True


def test_executor_auth_cannot_be_disabled_while_shadow_boundary_installed(
    tmp_path, monkeypatch,
):
    signer = Ed25519PrivateKey.generate()
    app, observed, _ = _app(tmp_path, monkeypatch, signer)
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "false")
    with TestClient(app) as client:
        result = _request(client, _token(signer))
    assert result.status_code == 503
    assert observed == []


def test_verified_scope_is_explicitly_not_physical_attestation():
    signer = Ed25519PrivateKey.generate()
    claims = verify_user_delegation(
        _token(signer),
        public_key=signer.public_key(),
        key_id=_KID,
        model="auto/code",
    )
    assert claims["subject"] == "alice"
    assert claims["attempt_id"] != claims["root_request_id"]
    assert "provider_execution_attested" not in claims
    assert "response_delivered" not in claims
    assert "provider_process" not in claims


@pytest.mark.parametrize("invalid_token", [
    "a.b.c",
    "!!.b.c",
    "a.b",
    "A" * 3100,
])
def test_malformed_delegation_is_rejected_without_server_error(
    tmp_path, monkeypatch, invalid_token,
):
    signer = Ed25519PrivateKey.generate()
    app, observed, ledger = _app(tmp_path, monkeypatch, signer)
    with TestClient(app) as client:
        response = _request(client, invalid_token)
    assert response.status_code == 401
    assert observed == []
    with sqlite3.connect(ledger.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM inference_request_idempotency"
        ).fetchone()[0] == 0


def test_large_delegated_request_is_rejected_before_downstream_execution(
    tmp_path, monkeypatch,
):
    signer = Ed25519PrivateKey.generate()
    app, observed, ledger = _app(tmp_path, monkeypatch, signer)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {_SERVICE}",
                "X-Auto-Router-User-Delegation": _token(signer),
            },
            json={
                "model": "auto/code",
                "messages": [{"role": "user", "content": "a" * 263000}],
                "max_tokens": 8,
            },
        )
    assert response.status_code == 401
    assert observed == []
    with sqlite3.connect(ledger.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM inference_request_idempotency"
        ).fetchone()[0] == 0
