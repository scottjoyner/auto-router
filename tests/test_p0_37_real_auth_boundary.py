"""Characterize REAL strict-live auth/idempotency P0 #37 gaps (test-only).

These are documentation-as-tests of present production middleware semantics.
They explicitly do NOT claim trusted per-human authentication or change them.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_executor_auth import _invoke, _token

from auto_router.executor_auth import ExecutorInferenceAuthMiddleware
from auto_router.request_idempotency import (
    RequestIdempotencyLedger,
    RequestIdempotencyMiddleware,
)


@pytest.mark.asyncio
async def test_internal_service_token_is_service_auth_not_human_auth(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "true")
    monkeypatch.setenv("AUTO_ROUTER_INTERNAL_SERVICE_TOKEN", "fixture-shared-service-key")
    observed = []
    for claimed_human in ("alice", "bob"):
        sent, captured = await _invoke(
            ExecutorInferenceAuthMiddleware,
            "fixture-shared-service-key",
            {
                "model": "auto/code",
                "messages": [{"role": "user", "content": "synthetic"}],
                "user": claimed_human,
                "metadata": {
                    "subject_id": claimed_human,
                    "tenant_id": "same-tenant",
                    "root_request_id": "forged-root",
                    "provider_execution_attested": True,
                },
            },
        )
        assert sent[0]["status"] == 204
        assert captured["metadata"]["assistx_service"] == {
            "identity": "assistx-internal",
            "authenticated": True,
        }
        # The raw caller assertion can transit to routing input. It is NOT
        # verified or promoted by this middleware into human authentication.
        assert captured["metadata"]["subject_id"] == claimed_human
        assert captured["metadata"]["root_request_id"] == "forged-root"
        assert "authenticated_human" not in captured["metadata"]
        assert "human_subject_verified" not in captured["metadata"]
        observed.append(captured["metadata"]["assistx_service"])
    assert observed[0] == observed[1]


@pytest.fixture
def isolated_executor_signer(monkeypatch):
    private = Ed25519PrivateKey.generate()
    public_key_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_VERIFY_KEY_PEM", public_key_pem)
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_KEY_ID", "test-key")
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "true")
    return private


@pytest.mark.asyncio
async def test_executor_claim_auth_uses_agent_task_not_human_subject(
    isolated_executor_signer, monkeypatch,
):
    monkeypatch.delenv("AUTO_ROUTER_INTERNAL_SERVICE_TOKEN", raising=False)
    for claimed_human in ("alice", "bob"):
        sent, captured = await _invoke(
            ExecutorInferenceAuthMiddleware,
            _token(isolated_executor_signer),
            {
                "model": "auto/code",
                "messages": [{"role": "user", "content": "synthetic"}],
                "max_tokens": 8,
                "metadata": {
                    "subject_id": claimed_human,
                    "tenant_id": "same-tenant",
                },
            },
        )
        assert sent[0]["status"] == 204
        lineage = captured["metadata"]["assistx_executor"]
        assert lineage["agent_id"] == "hermes-test"
        assert lineage["task_id"] == "task-1"
        assert lineage["claim_id"] == "claim-1"
        assert "subject_id" not in lineage
        assert "tenant_id" not in lineage
        assert captured["metadata"]["subject_id"] == claimed_human


def test_one_shared_service_bearer_has_one_idempotency_namespace_for_two_claimed_users(
    tmp_path,
):
    """Caller metadata changes do not cryptographically split service ownership."""
    database = tmp_path / "service-token-idempotency.sqlite"
    ledger = RequestIdempotencyLedger(f"sqlite:///{database}")
    app = FastAPI()
    forwarded = []

    @app.post("/v1/chat/completions")
    async def receive(request: Request):
        forwarded.append(await request.json())
        return {"id": "fixture", "choices": []}

    app.add_middleware(RequestIdempotencyMiddleware, ledger=ledger)
    with TestClient(app) as client:
        headers = {
            "Authorization": "Bearer fixture-shared-service-key",
            "Idempotency-Key": "client-shared-id",
        }
        response_a = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "auto/code", "user": "alice", "messages": []},
        )
        response_b = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "auto/code", "user": "bob", "messages": []},
        )
    assert response_a.status_code == 200
    assert response_b.status_code == 409
    assert len(forwarded) == 1
    assert "different request" in response_b.json()["detail"]
    with sqlite3.connect(database) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM inference_request_idempotency"
        ).fetchone()[0]
    assert rows == 1
    # This protects shared-service idempotency, but is NOT per-human separation.
