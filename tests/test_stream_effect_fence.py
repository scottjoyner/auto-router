"""Default-off real Auto-Router stream wrapper and fail-closed effect gate.

The test authority is NOT a real trusted broker. These tests prove adapter
behavior only; they do not validate model cancellation or client delivery.
"""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
import pytest

from auto_router.stream_effect_fence import (
    StreamEffectDenied, effect_fence_required, require_authority, authorize_chunk
)
from test_stream_lifecycle import _module, _request, _candidate


class _Authority:
    def __init__(self, *, reject_on=0, preflight_ok=True, offline_on=0, delay=0):
        self.reject_on=reject_on
        self.preflight_ok=preflight_ok
        self.offline_on=offline_on
        self.delay=delay
        self.dispatches=0
        self.checks=[]
        self.abort_calls=0

    async def preflight(self, *, request, candidate):
        self.dispatches+=1
        return self.preflight_ok

    async def accept_chunk(self, *, request, candidate, sequence, chunk_sha256):
        self.checks.append((sequence,chunk_sha256))
        if self.delay:
            await asyncio.sleep(self.delay)
        if sequence==self.offline_on:
            raise OSError("simulated partition")
        return sequence!=self.reject_on

    async def abort(self, *, request):
        self.abort_calls+=1
        return True


def _on(monkeypatch, authority=None):
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_STREAM_EFFECT_FENCE","true")
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING","true")


def _bytes_body(*chunks, after_chunk=None):
    async def run():
        for i,chunk in enumerate(chunks):
            yield chunk
        if after_chunk is not None:
            after_chunk()
    return run


@pytest.mark.asyncio
async def test_strict_stream_two_signed_digest_acknowledgements(monkeypatch):
    _on(monkeypatch)
    upstream_closed=[]
    async def body():
        try:
            yield b"first"
            yield b"second"
        finally:
            upstream_closed.append(True)
    module,*_= _module(body)
    authority=_Authority()
    module.state.stream_effect_authority=authority
    response=await module._dispatch_stream(None,_candidate(),_request())
    assert [c async for c in response.body]==[b"first",b"second"]
    assert authority.dispatches==1
    assert authority.checks==[(1,hashlib.sha256(b"first").hexdigest()),
                              (2,hashlib.sha256(b"second").hexdigest())]
    assert authority.abort_calls==0
    assert upstream_closed==[True]


@pytest.mark.asyncio
async def test_strict_revoked_chunk_never_leaks_and_upstream_closes(monkeypatch):
    _on(monkeypatch)
    upstream_closed=[]
    async def body():
        try:
            yield b"first"
            yield b"REVOKED_SECRET_SECOND"
            yield b"never"
        finally:
            upstream_closed.append(True)
    module,_,_,policy,circuits,quota,idempotency,outbox=_module(body)
    authority=_Authority(reject_on=2)
    module.state.stream_effect_authority=authority
    response=await module._dispatch_stream(None,_candidate(),_request())
    accepted=[]
    with pytest.raises(StreamEffectDenied,match="authority unavailable or denied"):
        async for chunk in response.body:
            accepted.append(chunk)
    assert accepted==[b"first"]
    assert authority.checks[0][0]==1 and authority.checks[1][0]==2
    assert authority.abort_calls==1
    assert upstream_closed==[True]
    assert quota.releases
    assert outbox.events[-1].event_type=="router.stream.failed"
    assert outbox.events[-1].payload["chunks_sent"]==1
    assert outbox.events[-1].payload["acceptance_state"]=="possibly_accepted"


@pytest.mark.asyncio
async def test_partition_denies_next_chunk_and_cancels_upstream(monkeypatch):
    _on(monkeypatch)
    closed=[]
    async def body():
        try:
            yield b"allowed-first"
            yield b"partitioned-second"
        finally:
            closed.append(True)
    module,*_= _module(body)
    authority=_Authority(offline_on=2)
    module.state.stream_effect_authority=authority
    response=await module._dispatch_stream(None,_candidate(),_request())
    output=[]
    with pytest.raises(StreamEffectDenied):
        async for chunk in response.body:
            output.append(chunk)
    assert output==[b"allowed-first"]
    assert authority.abort_calls==1
    assert closed==[True]


@pytest.mark.asyncio
async def test_missing_trusted_authority_denies_before_provider(monkeypatch):
    _on(monkeypatch)
    reached=[]
    async def body():
        reached.append("called")
        yield b"unverified"
    module,*_= _module(body)
    with pytest.raises(StreamEffectDenied,match="authority unavailable"):
        await module._dispatch_stream(None,_candidate(),_request())
    assert reached==[]


@pytest.mark.asyncio
async def test_missing_owner_fence_denies_before_dispatch(monkeypatch):
    _on(monkeypatch)
    monkeypatch.delenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING")
    module,*_= _module(_bytes_body(b"x"))
    module.state.stream_effect_authority=_Authority()
    with pytest.raises(StreamEffectDenied,match="strict authenticated owner"):
        await module._dispatch_stream(None,_candidate(),_request())


@pytest.mark.asyncio
async def test_preflight_rejection_denies_before_provider(monkeypatch):
    _on(monkeypatch)
    module,*_=_module(_bytes_body(b"x"))
    authority=_Authority(preflight_ok=False)
    module.state.stream_effect_authority=authority
    with pytest.raises(StreamEffectDenied,match="preflight"):
        await module._dispatch_stream(None,_candidate(),_request())
    assert authority.dispatches==1
    assert authority.checks==[]
    assert authority.abort_calls==0


@pytest.mark.asyncio
async def test_untrusted_metadata_authority_not_used(monkeypatch):
    _on(monkeypatch)
    module,*_=_module(_bytes_body(b"x"))
    request=_request()
    request.metadata["stream_effect_authority"]=_Authority()
    with pytest.raises(StreamEffectDenied,match="authority unavailable"):
        await module._dispatch_stream(None,_candidate(),request)


@pytest.mark.asyncio
async def test_strict_client_closes_early_triggers_abort(monkeypatch):
    _on(monkeypatch)
    closed=[]
    async def body():
        try:
            yield b"first"
            yield b"second"
        finally:
            closed.append(True)
    module,*_=_module(body)
    authority=_Authority()
    module.state.stream_effect_authority=authority
    response=await module._dispatch_stream(None,_candidate(),_request())
    stream=response.body
    assert await anext(stream)==b"first"
    await stream.aclose()
    assert authority.abort_calls==1
    assert closed==[True]
    assert [seq for seq,digest in authority.checks]==[1]


@pytest.mark.asyncio
async def test_off_by_default_preserves_existing_stream(monkeypatch):
    monkeypatch.delenv("AUTO_ROUTER_REQUIRE_STREAM_EFFECT_FENCE",raising=False)
    monkeypatch.delenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING",raising=False)
    module,*_=_module(_bytes_body(b"baseline"))
    response=await module._dispatch_stream(None,_candidate(),_request())
    assert [chunk async for chunk in response.body]==[b"baseline"]


def test_misspelled_flag_fails_closed(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_STREAM_EFFECT_FENCE","tru")
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING","true")
    assert effect_fence_required()
    with pytest.raises(StreamEffectDenied):
        require_authority(SimpleNamespace())


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk",[b"", "unsafe", bytearray(b"bytes")])
async def test_strict_gate_rejects_invalid_chunks(monkeypatch,chunk):
    _on(monkeypatch)
    module,*_=_module(_bytes_body(chunk))
    authority=_Authority()
    module.state.stream_effect_authority=authority
    response=await module._dispatch_stream(None,_candidate(),_request())
    with pytest.raises(StreamEffectDenied,match="invalid"):
        async for _part in response.body:
            pytest.fail("unverified chunk exposed")
    assert authority.abort_calls==1
    assert authority.checks==[]


@pytest.mark.asyncio
async def test_token_seq_denies_over_maximum_without_calling_broker(monkeypatch):
    authority=_Authority()
    with pytest.raises(StreamEffectDenied,match="sequence invalid"):
        await authorize_chunk(authority,_request(),_candidate(),b"data",100001)
    assert authority.checks==[]


@pytest.mark.asyncio
async def test_broker_timeout_blocks_chunk(monkeypatch):
    _on(monkeypatch)
    module,*_=_module(_bytes_body(b"cannot-release"))
    authority=_Authority(delay=10)
    module.state.stream_effect_authority=authority
    response=await module._dispatch_stream(None,_candidate(),_request())
    with pytest.raises(StreamEffectDenied,match="unavailable"):
        async for _chunk in response.body:
            pytest.fail("timeout should block output")
    assert authority.abort_calls==1


@pytest.mark.asyncio
async def test_nonstream_cannot_bypass_strict_effect_gate(monkeypatch):
    _on(monkeypatch)
    module,*_=_module(_bytes_body(b"no-leak"))
    request=_request(stream=False)
    with pytest.raises(StreamEffectDenied,match="non-streaming inference"):
        await module._dispatch(None,_candidate(),request)


@pytest.mark.asyncio
async def test_untrusted_abort_ack_does_not_expose_revoked_bytes(monkeypatch):
    _on(monkeypatch)
    module,*_=_module(_bytes_body(b"ok",b"denied"))
    authority=_Authority(reject_on=2)
    async def bad_abort(*,request):
        authority.abort_calls+=1
        return False
    authority.abort=bad_abort
    module.state.stream_effect_authority=authority
    response=await module._dispatch_stream(None,_candidate(),_request())
    observed=[]
    with pytest.raises(StreamEffectDenied):
        async for chunk in response.body:
            observed.append(chunk)
    assert observed==[b"ok"]
    assert authority.abort_calls==1


@pytest.mark.asyncio
async def test_abort_callback_failure_remains_fail_closed(monkeypatch):
    _on(monkeypatch)
    module,*_=_module(_bytes_body(b"first",b"denied"))
    authority=_Authority(reject_on=2)
    async def exploding_abort(*,request):
        authority.abort_calls+=1
        raise OSError("simulated upstream abort failure")
    authority.abort=exploding_abort
    module.state.stream_effect_authority=authority
    response=await module._dispatch_stream(None,_candidate(),_request())
    chunks=[]
    with pytest.raises(StreamEffectDenied):
        async for chunk in response.body:
            chunks.append(chunk)
    assert chunks==[b"first"]
    assert authority.abort_calls==1


@pytest.mark.asyncio
async def test_nonstream_baseline_unchanged_without_strict_mode(monkeypatch):
    monkeypatch.delenv("AUTO_ROUTER_REQUIRE_STREAM_EFFECT_FENCE",raising=False)
    module,*_=_module(_bytes_body(b"stream"))
    result=await module._dispatch(None,_candidate(),_request(stream=False))
    assert result.status_code==200


@pytest.mark.asyncio
async def test_missing_effect_fence_cannot_be_satisfied_by_request_payload(monkeypatch):
    _on(monkeypatch)
    module,*_=_module(_bytes_body(b"secret"))
    request=_request()
    request.raw_body["effect_fence_authority"]={"authorized":True}
    with pytest.raises(StreamEffectDenied,match="authority unavailable"):
        await module._dispatch_stream(None,_candidate(),request)


@pytest.mark.asyncio
async def test_real_provider_adapter_closes_http_transport_on_fenced_abort():
    """Proves the production provider's context/client-close path, not model abort."""
    from auto_router.providers import OpenAICompatibleProvider
    from auto_router.stream_effect_fence import abort_upstream

    marks=[]
    class FakeResponse:
        status_code=200
        headers={"content-type":"text/event-stream"}
        async def aiter_raw(self):
            try:
                yield b"first-HTTP-event"
                yield b"pending-second"
            finally:
                marks.append("raw_iterator_closed")
    class FakeContext:
        async def __aenter__(self):
            marks.append("http_stream_entered")
            return FakeResponse()
        async def __aexit__(self,*args):
            marks.append("http_stream_exited")
    class FakeHttpClient:
        def stream(self,*args,**kwargs):
            marks.append("http_stream_opened")
            return FakeContext()
        async def aclose(self):
            marks.append("client_closed")
    provider=OpenAICompatibleProvider(SimpleNamespace(
        name="research-only",type="openai",base_url="http://127.0.0.1/test",
        headers={},api_key_env=None,
    ))
    provider._client=lambda:FakeHttpClient()
    response=await provider._stream_post("/chat/completions",{"stream":True},"test-model")
    assert await anext(response.body)==b"first-HTTP-event"
    auth=_Authority()
    assert await abort_upstream(auth,_request(),response.body) is True
    assert auth.abort_calls==1
    assert marks==["http_stream_opened","http_stream_entered",
                   "raw_iterator_closed","http_stream_exited","client_closed"]


@pytest.mark.asyncio
async def test_abort_not_proven_without_real_close_hook():
    from auto_router.stream_effect_fence import abort_upstream
    auth=_Authority()
    # An upstream without aclose cannot be claimed as stopped even if
    # an authority ACKs cancellation.
    assert await abort_upstream(auth,_request(),object()) is False
    assert auth.abort_calls==1
