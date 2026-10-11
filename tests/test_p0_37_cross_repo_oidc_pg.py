"""Optional cross-repo P0 #37 synthetic-IdP -> AssistX -> Auto-router -> PG proof.

Only runs with BOTH explicit sandbox gates and a mounted AssistX draft
module path. No live IdP accounts, production gateway, model execution or
NAS file is accessed. Uses two actual FastAPI ASGI middleware instances.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import uuid
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_p0_37_verified_user_delegation import _app

from auto_router.user_root_postgres import PostgresRootOwnerAuthority

pytest_plugins = ("test_p0_37_postgres_authority",)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _idp(private, subject: str) -> str:
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": "p0-disposable-idp"}
    body = {
        "iss": "https://idp.example.test",
        "aud": "assistx-human-gateway", "sub": subject,
        "iat": now, "nbf": now, "exp": now + 300,
    }
    message = ".".join([
        _b64(json.dumps(header, sort_keys=True).encode()),
        _b64(json.dumps(body, sort_keys=True).encode()),
    ])
    signature = private.sign(
        message.encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    return message + "." + _b64(signature)


def _assistx_gateway(gateway_private: Ed25519PrivateKey):
    candidate = os.environ.get("P0_37_TEST_ASSISTX_OIDC_MODULE", "")
    if (
        os.environ.get("P0_37_TEST_EPHEMERAL") != "1"
        or not candidate
        or not Path(candidate).is_absolute()
        or not Path(candidate).is_file()
    ):
        pytest.skip("cross-repo AssistX P0 fixture not mounted")
    spec = spec_from_file_location("p0_37_cross_repo_oidc", candidate)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    idp_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = idp_private.public_key().public_numbers()
    jwks = {
        "keys": [{
            "kty": "RSA", "kid": "p0-disposable-idp", "alg": "RS256",
            "n": _b64(public.n.to_bytes((public.n.bit_length()+7)//8, "big")),
            "e": _b64(public.e.to_bytes((public.e.bit_length()+7)//8, "big")),
        }]
    }
    roster = {
        "issuer": "https://idp.example.test",
        "users": [
            {"sub": "idp-a", "tenant": "shared", "subject": "alice",
             "kind": "human", "models": ["auto/code"]},
            {"sub": "idp-b", "tenant": "shared", "subject": "bob",
             "kind": "human", "models": ["auto/code"]},
        ],
    }
    gateway = module.VerifiedHumanGateway(
        jwks=jwks, roster=roster, gateway_key=gateway_private,
        gateway_kid="fixture-human-key-1",
        router_service_bearer="fixture-shared-service-token",
        router_url="https://router.example.test/v1/chat/completions",
        audience="assistx-human-gateway",
    )
    return idp_private, gateway, module.HumanGatewayDenied


def test_two_signed_test_idp_subjects_cross_actual_router_middleware_and_shared_pg(
    tmp_path, monkeypatch, disposable_pg,
):
    signer = Ed25519PrivateKey.generate()
    idp, gateway, GatewayDenied = _assistx_gateway(signer)
    owner_id = "p0-disposable-" + uuid.uuid4().hex[:15]
    authority_a = PostgresRootOwnerAuthority(
        disposable_pg, b"\x99" * 32, owner_id,
        bootstrap=True, require_tls=False,
    )
    authority_b = PostgresRootOwnerAuthority(
        disposable_pg, b"\x99" * 32, owner_id, require_tls=False,
    )
    folder_a, folder_b = tmp_path / "router-a", tmp_path / "router-b"
    folder_a.mkdir()
    folder_b.mkdir()
    app_a, observed_a, _ = _app(
        folder_a, monkeypatch, signer, owner_authority=authority_a
    )
    app_b, observed_b, _ = _app(
        folder_b, monkeypatch, signer, owner_authority=authority_b
    )

    prompt = {
        "model": "auto/code",
        "messages": [{"role": "user", "content": "synthetic-only canary"}],
        "max_tokens": 8,
    }

    async def experiment():
        return await asyncio.gather(
            gateway.forward(
                _idp(idp, "idp-a"), prompt,
                transport=httpx.ASGITransport(app=app_a),
            ),
            gateway.forward(
                _idp(idp, "idp-b"), prompt,
                transport=httpx.ASGITransport(app=app_b),
            ),
        )

    outcomes = asyncio.run(experiment())
    assert [code for code, _ in outcomes] == [200, 200]
    assert all(body["id"] == "fixture-completion" for _, body in outcomes)
    assert [row["actor"]["subject"] for row in observed_a] == ["alice"]
    assert [row["actor"]["subject"] for row in observed_b] == ["bob"]
    a, b = observed_a[0]["actor"], observed_b[0]["actor"]
    assert a["root_request_id"] != b["root_request_id"]
    assert a["attempt_id"] != b["attempt_id"]
    assert a["model_handle"] == b["model_handle"] == "auto/code"
    assert "provider_execution_attested" not in a
    assert "response_delivered" not in b

    # No spoofed "user" can accompany the gateway's model request.
    with pytest.raises(GatewayDenied, match="unsupported gateway body"):
        asyncio.run(gateway.forward(
            _idp(idp, "idp-a"), {**prompt, "user": "bob"},
            transport=httpx.ASGITransport(app=app_a),
        ))
    assert len(observed_a) == 1

    # A third IdP token for an unregistered person cannot reach the router.
    with pytest.raises(GatewayDenied):
        asyncio.run(gateway.forward(
            _idp(idp, "unknown-idp-user"), prompt,
            transport=httpx.ASGITransport(app=app_a),
        ))
    assert len(observed_a) == 1

    # Fail-closed on shared-owner-authority loss: no local SQLite fallback.
    authority_b._dsn = (
        "host=127.0.0.1 port=1 dbname=unreachable connect_timeout=1"
    )
    error_status, _ = asyncio.run(gateway.forward(
        _idp(idp, "idp-b"), prompt,
        transport=httpx.ASGITransport(app=app_b),
    ))
    assert error_status == 503
    assert len(observed_b) == 1
