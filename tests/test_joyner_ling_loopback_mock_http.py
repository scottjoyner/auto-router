"""Actual loopback HTTP/1.1 ASGI tests; all model and identity data disposable.

Never binds a LAN/Tailnet interface or connects to Joyner.
"""
from __future__ import annotations
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
from types import ModuleType
import httpx
import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
if "auto_router" not in sys.modules:
    package = ModuleType("auto_router")
    package.__path__ = [str(ROOT / "src/auto_router")]
    sys.modules["auto_router"] = package

def local_module(name):
    key = f"auto_router.{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(
        key, ROOT / "src" / "auto_router" / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module

receipt = local_module("joyner_ling_receipt_candidate")
shadow = local_module("joyner_ling_shadow_transport")

@asynccontextmanager
async def local_server(app):
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0,
        log_level="critical", access_log=False, lifespan="off",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started and server.servers and server.servers[0].sockets:
                break
            await asyncio.sleep(0.02)
        assert server.started and server.servers
        host, port = server.servers[0].sockets[0].getsockname()[:2]
        assert host == "127.0.0.1" and port > 0
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5.0)

def signed_fixture(tmp_path: Path, *, stream=True):
    key = Ed25519PrivateKey.generate()
    body = json.dumps({
        "model": "joyner-ling", "stream": stream,
        "messages": [{"role": "user", "content": "harmless disposable test string"}],
        "max_tokens": 8,
    }, separators=(",", ":"), sort_keys=True).encode()
    moment = int(time.time())
    claims = {
        "iss": "assistx", "aud": "joyner-ling", "scopes": ["inference:ling"],
        "model_alias": "joyner-ling", "max_attempts": 1, "max_output_tokens": 8,
        "iat": moment - 1, "nbf": moment - 1, "exp": moment + 50,
        "principal_id": "loopback-synthetic-1", "task_id": "loopback-task",
        "claim_id": "loopback-claim", "agent_id": "loopback-agent",
        "jti": "loopback-once", "projection_generation": 11,
        "request_sha256": hashlib.sha256(body).hexdigest(),
        "model_sha256": "c"*64,
    }
    header = {"alg": "EdDSA", "typ": "assistx-joyner-ling+jwt", "kid": "joyner-ling-v1"}
    encode=lambda x:base64.urlsafe_b64encode(
        json.dumps(x,sort_keys=True,separators=(",", ":")).encode()
    ).decode().rstrip("=")
    prefix=encode(header)+"."+encode(claims)
    token=prefix+"."+base64.urlsafe_b64encode(
        key.sign(prefix.encode())
    ).decode().rstrip("=")
    storage = tmp_path / "loopback-receipt.sqlite"
    def verify(presented, request):
        return receipt.validate_and_reserve(
            presented, request, public_key=key.public_key(),
            trusted_caller_principal="loopback-synthetic-1",
            trusted_projection_generation=11,
            trusted_running_model_sha256="c"*64,
            database_path=storage, now=moment,
        )
    return body, token, verify, storage

def test_live_loopback_sse_signed_replay_and_missing_token(tmp_path):
    async def scenario():
        body, token, verify, db=signed_fixture(tmp_path)
        hits = []
        async def backend(request, authorization):
            hits.append((request, authorization.jti))
            yield b'data: {"token":"'
            yield b'\xe2'
            yield b'\x82\xac'
            yield b'"}\n\n'
            yield b'data: [DONE]\n\n'
        app = shadow.LingShadowTransport(verify, backend)
        async with local_server(app) as addr:
            async with httpx.AsyncClient(timeout=3) as client:
                no_auth=await client.post(addr+"/v1/chat/completions",content=body)
                assert no_auth.status_code==401
                headers={"Authorization":"Bearer "+token}
                async with client.stream(
                    "POST",addr+"/v1/chat/completions",content=body,headers=headers
                ) as answer:
                    assert answer.status_code==200
                    assert answer.headers["content-type"].startswith("text/event-stream")
                    result=await answer.aread()
                assert result==b'data: {"token":"\xe2\x82\xac"}\n\ndata: [DONE]\n\n'
                replay=await client.post(addr+"/v1/chat/completions",content=body,headers=headers)
                assert replay.status_code==401
        assert hits==[(body,"loopback-once")]
        assert db.exists()
    asyncio.run(scenario())

def test_loopback_truncated_stream_emits_error_not_done(tmp_path):
    async def scenario():
        body,token,verify,_=signed_fixture(tmp_path)
        async def backend(request, authorization):
            yield b"data: partial\n\n"
        async with local_server(shadow.LingShadowTransport(verify,backend)) as addr:
            async with httpx.AsyncClient(timeout=3) as client:
                response=await client.post(
                    addr+"/v1/chat/completions",content=body,
                    headers={"Authorization":"Bearer "+token})
                assert response.status_code==200
                assert b"data: partial" in response.content
                assert b"event: error" in response.content
                assert b"[DONE]" not in response.content
    asyncio.run(scenario())

def test_loopback_nonstream_invalid_json_yields_502_before_headers(tmp_path):
    async def scenario():
        body,token,verify,_=signed_fixture(tmp_path,stream=False)
        async def backend(request, authorization):
            yield b'{"choices": '
        async with local_server(shadow.LingShadowTransport(verify,backend)) as addr:
            async with httpx.AsyncClient(timeout=3) as client:
                response=await client.post(
                    addr+"/v1/chat/completions",content=body,
                    headers={"Authorization":"Bearer "+token})
                assert response.status_code==502
                assert b"choices" not in response.content
    asyncio.run(scenario())

def test_loopback_path_denial_and_no_CORS(tmp_path):
    async def scenario():
        _,_,verify,_=signed_fixture(tmp_path)
        async def backend(request, authorization):
            raise AssertionError("must not dispatch")
            yield b"never"
        async with local_server(shadow.LingShadowTransport(verify,backend)) as addr:
            async with httpx.AsyncClient(timeout=3) as client:
                for method,path,expected in [
                    ("GET","/v1/models",404),
                    ("OPTIONS","/v1/chat/completions",405),
                    ("POST","/v1/embeddings",404),
                ]:
                    response=await client.request(method,addr+path)
                    assert response.status_code==expected
                    assert "access-control-allow-origin" not in response.headers
    asyncio.run(scenario())


def test_loopback_client_disconnect_cancels_mock_producer(tmp_path):
    async def scenario():
        body, token, verify, _ = signed_fixture(tmp_path)
        started = asyncio.Event()
        closed = asyncio.Event()

        async def backend(request, authorization):
            try:
                yield b"data: initial\n\n"
                started.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

        app = shadow.LingShadowTransport(verify, backend)
        async with local_server(app) as addr:
            async with httpx.AsyncClient(timeout=3) as client:
                async with client.stream(
                    "POST", addr + "/v1/chat/completions", content=body,
                    headers={"Authorization": "Bearer " + token},
                ) as response:
                    assert response.status_code == 200
                    chunks = response.aiter_bytes()
                    chunk = await asyncio.wait_for(anext(chunks), timeout=2)
                    assert chunk == b"data: initial\n\n"
                    assert started.is_set()
                # Context exit closed the HTTP socket before [DONE].
                await asyncio.wait_for(closed.wait(), timeout=2)
    asyncio.run(scenario())
