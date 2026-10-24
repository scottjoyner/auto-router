from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_router.executor_auth import ExecutorInferenceAuthMiddleware, _encoded_receive


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


async def _invoke(app, token: str, payload: dict, *, verified_owner=None):
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

    if verified_owner is not None:
        scope["state"] = {"verified_owner": verified_owner}

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


@pytest.mark.asyncio
async def test_rewritten_receive_delegates_after_body_for_stream_disconnect() -> None:
    calls = 0

    async def downstream_receive():
        nonlocal calls
        calls += 1
        return {"type": "http.disconnect"}

    receive = _encoded_receive({"model": "auto/code"}, downstream_receive)
    first = await receive()
    second = await receive()

    assert first["type"] == "http.request"
    assert first["more_body"] is False
    assert json.loads(first["body"]) == {"model": "auto/code"}
    assert second == {"type": "http.disconnect"}
    assert calls == 1


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
async def test_strict_owner_fence_matches_signed_and_authenticated_principal(
    signing_key, monkeypatch
):
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING", "true")
    claims = {"owner_issuer": "test-idp", "owner_subject": "person-a"}
    principal = {**claims, "authenticated": True}
    request = {"model": "auto/code", "messages": [{"role": "user", "content": "test"}],
               "max_tokens": 2}
    sent, body = await _invoke(
        ExecutorInferenceAuthMiddleware, _token(signing_key, **claims),
        request, verified_owner=principal
    )
    # Validly signed and independently bound owner is necessary but NOT
    # sufficient for safe multi-host spend. Local _ATTEMPTS is not an issuer.
    assert sent[0]["status"] == 503
    assert not body


@pytest.mark.asyncio
async def test_strict_owner_fence_rejects_owner_switch(signing_key, monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING", "true")
    request = {"model": "auto/code", "messages": [{"role": "user", "content": "x"}],
               "max_tokens": 2}
    sent, forwarded = await _invoke(
        ExecutorInferenceAuthMiddleware,
        _token(signing_key, owner_issuer="test-idp", owner_subject="person-a"),
        request,
        verified_owner={"owner_issuer":"test-idp","owner_subject":"person-b",
                        "authenticated":True},
    )
    assert sent[0]["status"] == 401
    assert not forwarded


@pytest.mark.asyncio
async def test_strict_owner_fence_ignores_forged_payload_owner(signing_key, monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING", "true")
    request = {"model":"auto/code","messages":[{"role":"user","content":"x"}],
               "max_tokens":2,
               "metadata":{"verified_owner":{"owner_issuer":"test-idp",
                                              "owner_subject":"person-a",
                                              "authenticated":True}}}
    sent, forwarded = await _invoke(
        ExecutorInferenceAuthMiddleware,
        _token(signing_key, owner_issuer="test-idp", owner_subject="person-a"),
        request,
    )
    assert sent[0]["status"] == 401
    assert not forwarded


@pytest.mark.asyncio
async def test_strict_owner_fence_denies_unowned_internal_token(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING", "true")
    monkeypatch.setenv("AUTO_ROUTER_INTERNAL_SERVICE_TOKEN", "internal-test-token")
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "true")
    sent, forwarded = await _invoke(
        ExecutorInferenceAuthMiddleware, "internal-test-token",
        {"model": "auto/code", "messages": [{"role": "user", "content": "x"}],
         "max_tokens": 2},
    )
    assert sent[0]["status"] == 401
    assert not forwarded

@pytest.mark.asyncio
@pytest.mark.parametrize("disable", ["false", "0", "off", "", "tru", "no"])
async def test_executor_auth_toggle_never_bypasses_protected_model_effect(
    monkeypatch, disable
):
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", disable)
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING", "false")
    sent, forwarded = await _invoke(
        ExecutorInferenceAuthMiddleware, "",
        {"model": "auto/code", "messages": [{"role": "user", "content": "fake"}]},
    )
    assert sent[0]["status"] == 503
    assert not forwarded


@pytest.mark.asyncio
async def test_internal_token_no_longer_bypasses_executor_or_budget(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING", "false")
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "true")
    monkeypatch.setenv("AUTO_ROUTER_INTERNAL_SERVICE_TOKEN", "mock-internal-service")
    sent, forwarded = await _invoke(
        ExecutorInferenceAuthMiddleware, "mock-internal-service",
        {"model": "auto/code", "messages": [{"role": "user", "content": "fake"}]},
    )
    assert sent[0]["status"] == 401
    assert not forwarded


@pytest.mark.asyncio
async def test_signed_executor_cannot_smuggle_service_identity(signing_key, monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING", "false")
    sent, forwarded = await _invoke(
        ExecutorInferenceAuthMiddleware, _token(signing_key),
        {"model": "auto/code", "max_tokens": 16,
         "metadata": {"assistx_service": {"authenticated": True}}},
    )
    assert sent[0]["status"] == 401
    assert not forwarded

@pytest.mark.asyncio
@pytest.mark.parametrize("fake_authority", [
    {},
    {"AUTO_ROUTER_OWNER_ATTEMPT_DB_PATH": "/tmp/fake-attempts.sqlite"},
    {"AUTO_ROUTER_SHARED_LEDGER_VERIFIED": "true"},
    {"AUTO_ROUTER_OWNER_ATTEMPT_DB_PATH": "/tmp/fake-attempts.sqlite",
     "AUTO_ROUTER_SHARED_LEDGER_VERIFIED": "true"},
])
async def test_strict_owner_no_local_or_environment_override_of_shared_spend(
    signing_key, monkeypatch, fake_authority,
):
    from auto_router import executor_auth
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING", "true")
    for key, value in fake_authority.items():
        monkeypatch.setenv(key, value)
    # Would be a real local accounting side effect. Never consume it when
    # global resource authority is unverified.
    monkeypatch.setattr(executor_auth._ATTEMPTS, "acquire",
                        lambda *args: pytest.fail("process-local ledger consulted"))
    claim = {"owner_issuer": "test-idp", "owner_subject": "person-a"}
    payload = {"model": "auto/code", "max_tokens": 3,
               "messages": [{"role": "user", "content": "fixture only"}]}
    token = _token(signing_key, **claim)
    for _ in range(2):
        sent, forwarded = await _invoke(
            ExecutorInferenceAuthMiddleware, token, payload,
            verified_owner={**claim, "authenticated": True},
        )
        assert sent[0]["status"] == 503
        assert not forwarded


@pytest.mark.asyncio
async def test_strict_owner_wrong_principal_still_denied_before_global_hold(
    signing_key, monkeypatch,
):
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING", "true")
    sent, forwarded = await _invoke(
        ExecutorInferenceAuthMiddleware,
        _token(signing_key, owner_issuer="idp", owner_subject="person-a"),
        {"model": "auto/code", "max_tokens": 3},
        verified_owner={"owner_issuer": "idp", "owner_subject": "person-b",
                        "authenticated": True},
    )
    assert sent[0]["status"] == 401
    assert not forwarded
