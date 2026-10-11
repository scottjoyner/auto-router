"""P0 #37: v2 gateway signature must attest the precise route and prompt.

Synthetic Ed25519 fixtures only. No provider executes, and all persistent
owner/idempotency stores are disposable local pytest tmp_path files.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from test_p0_37_verified_user_delegation import _SERVICE, _app, _token

from auto_router.user_delegation import delegated_body_digest

_PATH = "/v1/chat/completions"


def _payload():
    return {
        "model": "auto/code",
        "messages": [{"role": "user", "content": "The private synthetic request"}],
        "max_tokens": 8,
    }


def _delegation(signer, payload, *, changes=None, root=None):
    claims = {
        "schema": "p0-user-shadow-v2",
        "request_path": _PATH,
        "request_sha256": delegated_body_digest(_PATH, payload),
    }
    if changes:
        claims.update(changes)
    return _token(signer, "alice", root=root, claims_changes=claims)


def _post(client, token, body):
    return client.post(
        _PATH,
        headers={
            "Authorization": "Bearer " + _SERVICE,
            "X-Auto-Router-User-Delegation": token,
            "Idempotency-Key": "signed-fixture-attempt",
        },
        json=body,
    )


def test_valid_request_digest_authenticates_exact_prompt_before_reservation(
    tmp_path, monkeypatch,
):
    signer = Ed25519PrivateKey.generate()
    app, observed, ledger = _app(
        tmp_path, monkeypatch, signer, require_body_digest=True
    )
    body = _payload()
    delegation = _delegation(signer, body)
    with TestClient(app) as client:
        response = _post(client, delegation, body)
        duplicate = _post(client, delegation, body)
    assert [response.status_code, duplicate.status_code] == [200, 409]
    assert len(observed) == 1
    assert observed[0]["actor"]["request_sha256"] == delegated_body_digest(
        _PATH, body
    )
    with sqlite3.connect(ledger.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM inference_request_idempotency"
        ).fetchone()[0] == 1


@pytest.mark.parametrize("mutation", [
    lambda value: {
        **value, "messages": [{"role": "user", "content": "altered prompt"}]
    },
    lambda value: {**value, "max_tokens": 9},
    lambda value: {**value, "user": "alice"},
    lambda value: {**value, "metadata": {"subject": "alice"}},
])
def test_signature_cannot_be_reused_for_changed_prompt_or_caller_metadata(
    tmp_path, monkeypatch, mutation,
):
    signer = Ed25519PrivateKey.generate()
    app, observed, ledger = _app(
        tmp_path, monkeypatch, signer, require_body_digest=True
    )
    body = _payload()
    token = _delegation(signer, body)
    with TestClient(app) as client:
        tampered = _post(client, token, mutation(body))
        original = _post(client, token, body)
    assert [tampered.status_code, original.status_code] == [401, 200]
    assert len(observed) == 1
    with sqlite3.connect(ledger.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM inference_request_idempotency"
        ).fetchone()[0] == 1


@pytest.mark.parametrize("changes", [
    {"request_path": "/v1/embeddings"},
    {"request_sha256": "0" * 64},
    {"request_sha256": "not-a-sha256"},
    {"request_sha256": "A" * 64},
])
def test_wrong_signed_route_or_digest_denied_before_owner_reservation(
    tmp_path, monkeypatch, changes,
):
    signer = Ed25519PrivateKey.generate()
    app, observed, ledger = _app(
        tmp_path, monkeypatch, signer, require_body_digest=True
    )
    body = _payload()
    with TestClient(app) as client:
        rejected = _post(client, _delegation(signer, body, changes=changes), body)
    assert rejected.status_code == 401
    assert observed == []
    with sqlite3.connect(ledger.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM inference_request_idempotency"
        ).fetchone()[0] == 0


def test_v1_signature_not_accepted_by_actual_strict_deployment_boundary(
    tmp_path, monkeypatch,
):
    signer = Ed25519PrivateKey.generate()
    app, observed, ledger = _app(
        tmp_path, monkeypatch, signer, require_body_digest=True
    )
    with TestClient(app) as client:
        denied = _post(client, _token(signer, "alice"), _payload())
    assert denied.status_code == 401
    assert observed == []
    with sqlite3.connect(ledger.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM inference_request_idempotency"
        ).fetchone()[0] == 0


def test_router_v2_digest_matches_independent_assistx_golden_vector():
    # Asserted independently in AssistX's hosted identity workflow.
    # No shared import makes an accidental change visible in either repo.
    payload = {
        "model": "auto/code",
        "messages": [{"role": "user", "content": "synthetic-only canary"}],
        "max_tokens": 8,
    }
    assert delegated_body_digest(_PATH, payload) == (
        "6efd8e288e4698a4515ea0f1a882d0a6572831061eb09048dde70fd8f8c9c141"
    )



def test_distinct_signed_retry_attempt_cannot_change_prompt_on_same_root(
    tmp_path, monkeypatch,
):
    signer = Ed25519PrivateKey.generate()
    app, observed, ledger = _app(
        tmp_path, monkeypatch, signer, require_body_digest=True
    )
    root = str(uuid.uuid4())
    first_body = _payload()
    second_body = {
        **first_body, "messages": [
            {"role": "user", "content": "different signed prompt"}
        ],
    }
    with TestClient(app) as client:
        first = _post(client, _delegation(signer, first_body, root=root), first_body)
        second = _post(client, _delegation(signer, second_body, root=root), second_body)
        retry = _post(client, _delegation(signer, first_body, root=root), first_body)
    assert [first.status_code, second.status_code, retry.status_code] == [
        200, 409, 200
    ]
    assert len(observed) == 2
    assert observed[0]["actor"]["root_request_id"] == root
    assert observed[1]["actor"]["root_request_id"] == root
    assert (
        observed[0]["actor"]["request_sha256"]
        == observed[1]["actor"]["request_sha256"]
    )
    with sqlite3.connect(ledger.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM inference_request_idempotency"
        ).fetchone()[0] == 2
