"""Disposable ASGI-only Ling transport tests: NO sockets, models, or credentials."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "auto_router"
if PACKAGE not in sys.modules:
    pkg = ModuleType(PACKAGE)
    pkg.__path__ = [str(ROOT / "src/auto_router")]
    sys.modules[PACKAGE] = pkg


def load(name: str):
    identifier = f"auto_router.{name}"
    spec = importlib.util.spec_from_file_location(
        identifier, ROOT / "src" / "auto_router" / (name + ".py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[identifier] = module
    spec.loader.exec_module(module)
    return module


receipt = load("joyner_ling_receipt_candidate")
shadow = load("joyner_ling_shadow_transport")


def synthetic_receipt():
    return receipt.LingReceipt(
        principal_id="test-only-user", task_id="task-fixture",
        claim_id="claim-fixture", agent_id="agent-fixture",
        jti="token-fixture", projection_generation=4,
        model_sha256="a"*64, request_sha256="b"*64,
    )


BODY = json.dumps({
    "model":"joyner-ling", "stream":True,
    "messages":[{"role":"user","content":"test prompt must never leave memory"}],
    "max_tokens":32,
}).encode()


async def invoke(*, verifier=None, backend=None, headers=None,
                 body=BODY, path="/v1/chat/completions",
                 method="POST", extra_receive=None,
                 split_at=None, send_fn=None):
    captures={"requests":[], "tokens":[], "upstream_calls":0, "backend_closed":False}
    sent=[]
    queue=asyncio.Queue()
    if split_at is None:
        queue.put_nowait({"type":"http.request", "body":body, "more_body":False})
    else:
        queue.put_nowait({"type":"http.request", "body":body[:split_at],"more_body":True})
        queue.put_nowait({"type":"http.request", "body":body[split_at:],"more_body":False})
    for x in extra_receive or []:
        queue.put_nowait(x)

    async def receive():
        return await queue.get()

    async def send(x):
        if send_fn is not None:
            await send_fn(x)
        sent.append(x)

    def do_verify(token, contents):
        captures["tokens"].append(token)
        captures["requests"].append(contents)
        return verifier(token, contents) if verifier is not None else synthetic_receipt()

    async def do_backend(contents, r):
        captures["upstream_calls"] += 1
        try:
            if backend is not None:
                async for part in backend(contents,r):
                    yield part
            else:
                yield b"data: {"
                yield b'"text":"'
                yield b"\xe2"
                yield b"\x82\xac"
                yield b'"}\n\n'
                yield b"data: [DONE]\n\n"
        finally:
            captures["backend_closed"] = True

    h=headers if headers is not None else [(b"authorization",b"Bearer synthetic-jwt")]
    app=shadow.LingShadowTransport(do_verify,do_backend)
    await asyncio.wait_for(app(
        {"type":"http","path":path,"method":method,"headers":h},
        receive,send,
    ),timeout=3)
    return sent,captures


def status(messages):
    return [x["status"] for x in messages if x["type"]=="http.response.start"][0]


def reply_chunks(messages):
    return [x["body"] for x in messages if x["type"]=="http.response.body"]


def test_stream_byte_exact_including_split_utf8():
    sent,info=asyncio.run(invoke(split_at=13))
    assert status(sent)==200
    chunks=reply_chunks(sent)
    assert b"".join(chunks)==b'data: {"text":"\xe2\x82\xac"}\n\ndata: [DONE]\n\n'
    assert chunks[-1]==b""
    assert info["upstream_calls"]==1
    assert info["backend_closed"] is True
    assert info["requests"]==[BODY]


@pytest.mark.parametrize("headers",[
    [],[(b"authorization",b"Basic abc")],
    [(b"Authorization",b"Bearer a"),(b"authorization",b"Bearer b")],
    [(b"authorization",b"Bearer has whitespace")],
    [(b"authorization",b"Bearer \xff")],
])
def test_missing_duplicate_or_malformed_bearer_never_invokes_backend(headers):
    sent,info=asyncio.run(invoke(headers=headers))
    assert status(sent)==401
    assert info["upstream_calls"]==0
    assert info["requests"]==[]


def test_no_catalog_get_options_or_cors_promotion():
    for method,path,expected in (
        ("GET","/v1/models",404),
        ("OPTIONS","/v1/chat/completions",405),
        ("GET","/v1/chat/completions",405),
        ("POST","/v1/embeddings",404),
    ):
        sent,info=asyncio.run(invoke(method=method,path=path))
        assert status(sent)==expected
        assert info["upstream_calls"]==0
        assert not any(k.lower()==b"access-control-allow-origin"
                       for k,v in sent[0]["headers"])


def test_verification_exception_redacts_token_and_prompt():
    def reject(token,body):
        raise receipt.LingReceiptDenied("private model/credential detail")
    sent,info=asyncio.run(invoke(verifier=reject))
    assert status(sent)==401
    assert b"private" not in b"".join(reply_chunks(sent))
    assert BODY not in b"".join(reply_chunks(sent))
    assert info["upstream_calls"]==0


def test_invalid_receipt_object_denies_dispatch():
    sent,info=asyncio.run(invoke(verifier=lambda token,body: {"approved":True}))
    assert status(sent)==401 and info["upstream_calls"]==0


def test_content_length_ambiguity_and_transfer_encoding_denied():
    cases = [
        [(b"authorization",b"Bearer token"),(b"content-length",b"100"),
         (b"content-length",b"100")],
        [(b"authorization",b"Bearer token"),(b"transfer-encoding",b"chunked")],
        [(b"authorization",b"Bearer token"),(b"content-length",b"not-a-number")],
    ]
    for headers in cases:
        sent,info=asyncio.run(invoke(headers=headers))
        assert status(sent)==400
        assert info["upstream_calls"]==0


def test_request_over_64kb_denied_without_verification():
    sent,info=asyncio.run(invoke(body=b"x"*(64*1024+1)))
    assert status(sent)==413
    assert info["upstream_calls"]==0 and info["tokens"]==[]


def test_initial_backend_failure_is_502_without_prompt_leakage():
    async def fail(body,receipt_):
        raise RuntimeError("model prompt contains secret")
        yield b"never"
    sent,info=asyncio.run(invoke(backend=fail))
    assert status(sent)==502
    assert b"secret" not in b"".join(reply_chunks(sent))
    assert info["backend_closed"] is True


def test_client_disconnect_aborts_mock_stream_cleanly():
    async def backend(body, receipt_):
        yield b"data: first\n\n"
        await asyncio.Event().wait()
        yield b"not possible"
    sent,info=asyncio.run(invoke(
        backend=backend,
        extra_receive=[{"type":"http.disconnect"}],
    ))
    assert status(sent)==200
    assert reply_chunks(sent)==[b"data: first\n\n"]
    assert info["backend_closed"] is True


def test_mock_stream_cannot_inject_cors_or_authorization_headers():
    sent,_=asyncio.run(invoke())
    headers=sent[0]["headers"]
    keys={k.lower() for k,v in headers}
    assert b"authorization" not in keys
    assert b"access-control-allow-origin" not in keys
    assert (b"cache-control",b"no-store") in headers
    assert (b"x-content-type-options",b"nosniff") in headers


def test_bounded_stream_cannot_fake_successful_done_after_overflow():
    async def too_large(body,r):
        yield b"data: initial\n\n"
        yield b"x"*(1024*1024+1)
        yield b"data: [DONE]\n\n"
    sent,info=asyncio.run(invoke(backend=too_large))
    assert status(sent)==200
    assert b"[DONE]" not in b"".join(reply_chunks(sent))
    assert info["backend_closed"] is True


def test_real_synthetic_ed25519_receipt_admits_one_mock_request_only(tmp_path):
    import base64
    import hashlib
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv=Ed25519PrivateKey.generate()
    current=1_800_000_100
    model_sha="f"*64
    header={"alg":"EdDSA","typ":"assistx-joyner-ling+jwt","kid":"joyner-ling-v1"}
    claims={
        "iss":"assistx","aud":"joyner-ling","scopes":["inference:ling"],
        "model_alias":"joyner-ling","max_attempts":1,
        "iat":current-1,"nbf":current-1,"exp":current+45,
        "principal_id":"user-synthetic-1","task_id":"task-1",
        "claim_id":"claim-1","agent_id":"agent-1",
        "jti":"once-only-1","projection_generation":12,
        "request_sha256":hashlib.sha256(BODY).hexdigest(),
        "model_sha256":model_sha,"max_output_tokens":32
    }
    encode=lambda d:base64.urlsafe_b64encode(
        json.dumps(d,sort_keys=True,separators=(",",":")).encode()
    ).decode().rstrip("=")
    header_b=encode(header)
    claims_b=encode(claims)
    unsigned=(header_b+"."+claims_b).encode()
    token=unsigned.decode()+"."+base64.urlsafe_b64encode(
        priv.sign(unsigned)
    ).decode().rstrip("=")
    calls=[]
    def verify(token_value,body):
        calls.append("verify")
        return receipt.validate_and_reserve(
            token_value,body,public_key=priv.public_key(),
            trusted_caller_principal="user-synthetic-1",
            trusted_projection_generation=12,
            trusted_running_model_sha256=model_sha,
            database_path=tmp_path/"ling-requests.sqlite",now=current)
    h=[(b"authorization",("Bearer "+token).encode())]
    first,one=asyncio.run(invoke(verifier=verify,headers=h))
    second,two=asyncio.run(invoke(verifier=verify,headers=h))
    assert status(first)==200
    assert b"data: [DONE]" in b"".join(reply_chunks(first))
    assert one["upstream_calls"]==1
    assert status(second)==401
    assert two["upstream_calls"]==0
    assert calls==["verify","verify"]
    assert b"test prompt must never leave memory" not in (
        tmp_path/"ling-requests.sqlite").read_bytes()


def test_wrong_principal_signed_receipt_denied_before_backend(tmp_path):
    def deny_wrong_principal(token,body):
        raise receipt.LingReceiptDenied("caller_principal_mismatch")
    sent,data=asyncio.run(invoke(verifier=deny_wrong_principal))
    assert status(sent)==401
    assert data["upstream_calls"]==0
