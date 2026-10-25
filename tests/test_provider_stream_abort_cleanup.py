"""Pure-mock stream cancellation cleanup for both provider implementations.

Closing local HTTP transport is NOT proof of remote model-side cancellation.
"""
from __future__ import annotations

import pytest

from auto_router.models import ProviderConfig, RouterRequest
from auto_router.providers import OpenAICompatibleProvider, AgentGatewayProviderAdapter
from auto_router.gateway_config import GatewayConfig


class FakeRawResponse:
    status_code = 200
    headers = {"content-type": "text/event-stream"}

    def __init__(self, events, *, raw_failure=False):
        self.events = events
        self.raw_failure = raw_failure

    async def aiter_raw(self):
        try:
            yield b"one-synthetic-chunk"
            yield b"second-synthetic-chunk"
        finally:
            self.events.append("raw_iterator_closed")
            if self.raw_failure:
                raise OSError("synthetic nested raw iterator close error")


class FakeContext:
    def __init__(self, events, *, raw_failure=False, ctx_failure=False):
        self.events = events
        self.ctx_failure = ctx_failure
        self.raw = FakeRawResponse(events, raw_failure=raw_failure)

    async def __aenter__(self):
        self.events.append("stream_entered")
        return self.raw

    async def __aexit__(self, *_args):
        self.events.append("http_context_exited")
        if self.ctx_failure:
            raise OSError("synthetic HTTP context close error")


class FakeClient:
    def __init__(self, events, *, raw_failure=False, ctx_failure=False):
        self.events = events
        self.raw_failure = raw_failure
        self.ctx_failure = ctx_failure

    def stream(self, *_a, **_kw):
        self.events.append("http_stream_created")
        return FakeContext(
            self.events, raw_failure=self.raw_failure,
            ctx_failure=self.ctx_failure,
        )

    async def aclose(self):
        self.events.append("http_client_closed")


def fixtures(provider_kind, events, monkeypatch, *, raw_failure=False, ctx_failure=False):
    if provider_kind == "openai":
        provider = OpenAICompatibleProvider(
            ProviderConfig(name="synthetic",type="openai_compatible",
                           base_url="http://127.0.0.1/unused",quota_class="local")
        )
    else:
        provider = AgentGatewayProviderAdapter(
            ProviderConfig(id="synthetic-gateway",name="synthetic-gateway",
                           type="openai_compatible",
                           base_url="http://127.0.0.1/unused",
                           quota_class="local")
        )
    def fake_factory():
        return FakeClient(
            events, raw_failure=raw_failure, ctx_failure=ctx_failure
        )
    provider._client = fake_factory
    # AgentGatewayProviderAdapter constructs its HTTPX client directly,
    # bypassing _client(). Replace that constructor in the test only.
    if provider_kind == "agentgateway":
        monkeypatch.setattr(
            "auto_router.providers.httpx.AsyncClient",
            lambda *args, **kwargs: fake_factory(),
        )
    return provider


async def request_stream(provider, provider_kind):
    if provider_kind == "openai":
        return await provider._stream_post(
            "/chat/completions", {"model":"synthetic","stream":True}, "synthetic"
        )
    request=RouterRequest(
        request_id="fake-stream-request",
        route="chat_completions",
        model="synthetic",
        messages=[{"role":"user","content":"fixture"}],
        raw_body={"model":"synthetic","stream":True,
                  "messages":[{"role":"user","content":"fixture"}]},
    )
    gateway_config=GatewayConfig(
        enabled=True,mode="sidecar",base_url="http://127.0.0.1/unused",
        openai_base_url="http://127.0.0.1/unused/v1",
        timeout_seconds=1,
        fail_open_to_direct=False,fail_closed_for_private=True,
        emit_headers=True,pass_metadata_in_body=True,reconcile_usage=True,
    )
    return await provider.stream_chat_completions(
        request, "synthetic", gateway_config=gateway_config,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_kind",["openai","agentgateway"])
async def test_abort_closes_raw_before_context_and_client(provider_kind,monkeypatch):
    events=[]
    provider=fixtures(provider_kind,events,monkeypatch)
    response=await request_stream(provider,provider_kind)
    assert await anext(response.body)==b"one-synthetic-chunk"
    await response.body.aclose()
    assert events[-3:]==[
        "raw_iterator_closed","http_context_exited","http_client_closed"
    ]
    assert events.count("http_client_closed")==1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_kind",["openai","agentgateway"])
@pytest.mark.parametrize("raw_failure,ctx_failure",[
    (True,False),(False,True),(True,True)
])
async def test_abort_always_attempts_transport_close_on_nested_failures(
    provider_kind,raw_failure,ctx_failure,monkeypatch
):
    events=[]
    provider=fixtures(
        provider_kind,events,monkeypatch,raw_failure=raw_failure,
        ctx_failure=ctx_failure
    )
    response=await request_stream(provider,provider_kind)
    assert await anext(response.body)==b"one-synthetic-chunk"
    with pytest.raises(OSError):
        await response.body.aclose()
    assert events[-3:]==[
        "raw_iterator_closed","http_context_exited","http_client_closed"
    ]
    assert events.count("http_client_closed")==1
