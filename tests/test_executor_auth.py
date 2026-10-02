from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_router.executor_auth import (
    ExecutorAuthError,
    ExecutorInferenceAuthMiddleware,
    _load_public_key,
    decode_executor_token,
)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _token(private: Ed25519PrivateKey, **overrides) -> str:
    now = int(time.time())
    header = {"alg": "EdDSA", "kid": "test-key", "typ": "assistx-executor+jwt"}
    claims = {
        "iss": "assistx",
        "aud": ["assistx-executor", "auto-router"],
        "iat": now,
        "nbf": now,
        "exp": now + 300,
        "jti": f"jti-{time.time_ns()}",
        "task_id": "task-1",
        "claim_id": "claim-1",
        "agent_id": "hermes-test",
        "projection_generation": 4,
        "scopes": ["inference"],
        "allowed_model_aliases": ["auto/code"],
        "max_input_tokens": 4096,
        "max_output_tokens": 512,
        "max_attempts": 2,
    }
    claims.update(overrides)
    encoded_header = _b64(json.dumps(header, sort_keys=True, separators=(",", ":")).encode())
    encoded_claims = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    return f"{encoded_header}.{encoded_claims}.{_b64(private.sign(signing_input))}"


class _Generation:
    generation = 4

    @staticmethod
    def is_fresh():
        return True


async def _invoke(app, token: str, payload: dict):
    sent = []
    captured = {}
    encoded = json.dumps(payload).encode()
    used = False

    async def receive():
        nonlocal used
        if used:
            return {"type": "http.disconnect"}
        used = True
        return {"type": "http.request", "body": encoded, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }

    async def downstream(inner_scope, inner_receive, inner_send):
        body = await inner_receive()
        captured.update(json.loads(body["body"]))
        await inner_send({"type": "http.response.start", "status": 204, "headers": []})
        await inner_send({"type": "http.response.body", "body": b""})

    middleware = ExecutorInferenceAuthMiddleware(
        downstream,
        SimpleNamespace(runtime_projection_manager=SimpleNamespace(current=_Generation())),
    )
    await middleware(scope, receive, send)
    return sent, captured


@pytest.fixture
def signing_key(monkeypatch):
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_VERIFY_KEY_PEM", public_pem)
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_KEY_ID", "test-key")
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "true")
    return private


@pytest.mark.asyncio
async def test_inference_token_injects_nonsecret_execution_lineage(signing_key):
    sent, captured = await _invoke(
        ExecutorInferenceAuthMiddleware,
        _token(signing_key),
        {
            "model": "auto/code",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 128,
        },
    )
    assert sent[0]["status"] == 204
    lineage = captured["metadata"]["assistx_executor"]
    assert lineage["task_id"] == "task-1"
    assert lineage["claim_id"] == "claim-1"
    assert lineage["projection_generation"] == 4
    assert lineage["attempt"] == 1


@pytest.mark.asyncio
async def test_inference_token_rejects_wrong_model_and_stale_generation(signing_key):
    wrong_model, _ = await _invoke(
        ExecutorInferenceAuthMiddleware,
        _token(signing_key),
        {
            "model": "auto/review",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 128,
        },
    )
    assert wrong_model[0]["status"] == 401

    stale, _ = await _invoke(
        ExecutorInferenceAuthMiddleware,
        _token(signing_key, projection_generation=3),
        {
            "model": "auto/code",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 128,
        },
    )
    assert stale[0]["status"] == 401


@pytest.mark.asyncio
async def test_inference_token_enforces_output_and_attempt_budgets(signing_key):
    oversized, _ = await _invoke(
        ExecutorInferenceAuthMiddleware,
        _token(signing_key, max_output_tokens=64),
        {
            "model": "auto/code",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 128,
        },
    )
    assert oversized[0]["status"] == 401

    token = _token(signing_key, max_attempts=1)
    first, _ = await _invoke(
        ExecutorInferenceAuthMiddleware,
        token,
        {
            "model": "auto/code",
            "messages": [{"role": "user", "content": "one"}],
            "max_tokens": 32,
        },
    )
    second, _ = await _invoke(
        ExecutorInferenceAuthMiddleware,
        token,
        {
            "model": "auto/code",
            "messages": [{"role": "user", "content": "two"}],
            "max_tokens": 32,
        },
    )
    assert first[0]["status"] == 204
    assert second[0]["status"] == 401


@pytest.mark.asyncio
async def test_unreadable_verify_key_file_rejects_rather_than_raising(signing_key, monkeypatch):
    """A missing key file must be rejected, never raised through the middleware.

    ``docker-compose.integration.yml`` inherits
    ``AUTO_ROUTER_EXECUTOR_VERIFY_KEY_FILE`` from the base compose file while
    replacing ``volumes:`` with ``!override``, so the PEM is absent in the smoke
    container. The request must be refused without reaching the downstream app.
    """

    monkeypatch.setenv(
        "AUTO_ROUTER_EXECUTOR_VERIFY_KEY_FILE",
        "/nonexistent/run/secrets/assistx_executor_public_key.pem",
    )
    monkeypatch.delenv("AUTO_ROUTER_EXECUTOR_VERIFY_KEY_PEM", raising=False)

    sent, captured = await _invoke(
        ExecutorInferenceAuthMiddleware,
        _token(signing_key),
        {
            "model": "auto/code",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 128,
        },
    )

    assert sent[0]["status"] == 401
    assert captured == {}, "the request must never reach the downstream app"
    # The specific cause is deliberately not leaked to callers: `decode_executor_token`
    # re-wraps verification failures as a generic signature rejection.
    body = next(message.get("body") for message in sent if "body" in message)
    assert json.loads(body)["detail"] == "executor token signature is invalid"


def test_unreadable_verify_key_file_is_distinguishable_from_a_bad_token(signing_key, monkeypatch):
    """The rejection must identify the deployment fault, not the caller.

    ``decode_executor_token`` masks verification failures as
    "signature is invalid". When the router's own key material is unreadable the
    chained cause has to stay an ``ExecutorAuthError`` so operators can tell a
    missing/unreadable key file apart from a genuinely invalid token.
    """

    monkeypatch.setenv(
        "AUTO_ROUTER_EXECUTOR_VERIFY_KEY_FILE",
        "/nonexistent/run/secrets/assistx_executor_public_key.pem",
    )
    monkeypatch.delenv("AUTO_ROUTER_EXECUTOR_VERIFY_KEY_PEM", raising=False)

    with pytest.raises(ExecutorAuthError) as caught:
        decode_executor_token(_token(signing_key))

    assert isinstance(caught.value.__cause__, ExecutorAuthError)
    assert "verification key file is unreadable" in str(caught.value.__cause__)
    assert isinstance(caught.value.__cause__.__cause__, OSError)


def test_verify_key_file_oserror_is_wrapped_as_executor_auth_error(monkeypatch):
    monkeypatch.setenv(
        "AUTO_ROUTER_EXECUTOR_VERIFY_KEY_FILE",
        "/nonexistent/run/secrets/assistx_executor_public_key.pem",
    )
    monkeypatch.delenv("AUTO_ROUTER_EXECUTOR_VERIFY_KEY_PEM", raising=False)

    with pytest.raises(ExecutorAuthError, match="verification key file is unreadable"):
        _load_public_key()
