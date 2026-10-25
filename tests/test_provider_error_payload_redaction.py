"""Synthetic provider-error redaction acceptance, no network or model requests.

Load repository provider implementations without starting Auto-Router or
modifying its production runtime. MockTransport supplies adversarial
responses whose error body and headers contain fake credential-like strings.
"""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1] / "src" / "auto_router"
SECRET = "SYNTHETIC_PROVIDER_SECRET_NEVER_LOG_123"
LEAK_HEADERS = {"Set-Cookie": "session=" + SECRET,
                "X-Leaked-Credential": SECRET,
                "Retry-After": "30"}
LEAK_BODY = ("pretend upstream reflected raw prompt and bearer " + SECRET).encode()


def _load_modules_without_bootstrap():
    package = types.ModuleType("auto_router")
    package.__path__ = [str(ROOT)]
    package.__package__ = "auto_router"
    modules = {}
    with patch.dict(sys.modules, {"auto_router": package}):
        for name in ("models", "provider_credential_file", "providers", "gateway"):
            full = "auto_router." + name
            spec = importlib.util.spec_from_file_location(full, ROOT / (name + ".py"))
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[full] = module
            spec.loader.exec_module(module)
            modules[name] = module
    return modules


MOD = _load_modules_without_bootstrap()
ProviderConfig = MOD["models"].ProviderConfig
RouterRequest = MOD["models"].RouterRequest
OpenAICompatibleProvider = MOD["providers"].OpenAICompatibleProvider
Gateway = MOD["providers"].AgentGatewayProviderAdapter
ProviderError = MOD["providers"].ProviderError


@pytest.fixture()
def fake_http(monkeypatch):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            429, headers=LEAK_HEADERS, content=LEAK_BODY, request=request,
        )
    )
    original = httpx.AsyncClient

    def make_client(*args, **kwargs):
        kwargs.pop("transport", None)
        return original(*args, transport=transport, **kwargs)
    monkeypatch.setattr(MOD["providers"].httpx, "AsyncClient", make_client)
    return make_client


def _request():
    return RouterRequest(
        request_id="offline-negative", route="chat_completions",
        raw_body={"model": "fake-model", "messages": [{"role": "user", "content": "FAKE"}]},
    )


def _provider():
    return OpenAICompatibleProvider(
        ProviderConfig(
            name="offline-provider", type="openai_compatible",
            base_url="http://synthetic.invalid/v1", enabled=True,
        )
    )


def _gateway():
    return Gateway(ProviderConfig(
        name="offline-gateway", id="fake-sidecar", type="openai_compatible",
        base_url="http://synthetic.invalid/v1",
    ))


def _gateway_options():
    return SimpleNamespace(
        openai_base_url="http://synthetic.invalid/v1",
        timeout_seconds=10,
        pass_metadata_in_body=False,
        fail_open_to_direct=False,
    )


@pytest.mark.parametrize("path", ["discovery", "post", "stream", "gateway_post", "gateway_stream"])
def test_upstream_secret_never_enters_error_or_metadata(path, fake_http):
    p = _gateway() if path.startswith("gateway_") else _provider()

    async def trial():
        if path == "discovery":
            await p.list_models()
        elif path == "post":
            await p.chat_completions(_request(), "fake-model")
        elif path == "stream":
            await p.stream_chat_completions(_request(), "fake-model")
        elif path == "gateway_post":
            await p.chat_completions(_request(), "fake-model",
                                     gateway_config=_gateway_options())
        else:
            await p.stream_chat_completions(_request(), "fake-model",
                                            gateway_config=_gateway_options())

    # Gateway imports occur at call-time; use actual gateway module from
    # repository, not dummy header construction.
    with patch.dict(sys.modules, {"auto_router.gateway": MOD["gateway"]}):
        with pytest.raises(ProviderError) as caught:
            asyncio.run(trial())
    exc = caught.value
    assert exc.status_code == 429
    assert exc.retryable is True
    assert "HTTP 429" in str(exc)
    assert SECRET not in str(exc)
    assert SECRET not in repr(exc)
    assert SECRET not in str(exc.headers)
    assert exc.headers == {}
    if not path.startswith("gateway_"):
        assert exc.retry_after_seconds == 30
    assert LEAK_BODY.decode() not in str(exc)


@pytest.mark.parametrize("status,expected_retry", [
    (401, False), (403, False), (429, True), (503, True),
])
def test_status_remains_actionable_without_body_or_headers(
    monkeypatch, status, expected_retry,
):
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            status, headers=LEAK_HEADERS, content=LEAK_BODY, request=r,
        )
    )
    real = httpx.AsyncClient
    monkeypatch.setattr(
        MOD["providers"].httpx, "AsyncClient",
        lambda *args, **kw: real(*args, transport=transport, **kw),
    )
    with pytest.raises(ProviderError) as caught:
        asyncio.run(_provider().chat_completions(_request(), "fake-model"))
    exc = caught.value
    assert exc.status_code == status
    assert exc.retryable is expected_retry
    assert exc.retry_after_seconds == 30
    assert exc.headers == {}
    assert SECRET not in str(exc)


@pytest.mark.parametrize("route", ["direct", "gateway"])
def test_stream_http_error_closes_without_reading_upstream_body(route, monkeypatch):
    reads = []

    class ExplosiveBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            reads.append("read")
            raise AssertionError("upstream error body must not be consumed")
            yield b"unused"

        async def aclose(self):
            reads.append("closed")

    def respond(request):
        return httpx.Response(
            403,
            headers={"Set-Cookie": SECRET, "Retry-After": "30"},
            stream=ExplosiveBody(),
            request=request,
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        MOD["providers"].httpx, "AsyncClient",
        lambda *a, **kw: original(*a, transport=httpx.MockTransport(respond), **kw),
    )
    provider = _gateway() if route == "gateway" else _provider()

    async def exercise():
        if route == "gateway":
            await provider.stream_chat_completions(
                _request(), "fake-model", gateway_config=_gateway_options()
            )
        else:
            await provider.stream_chat_completions(_request(), "fake-model")

    with patch.dict(sys.modules, {"auto_router.gateway": MOD["gateway"]}):
        with pytest.raises(ProviderError) as caught:
            asyncio.run(exercise())
    assert caught.value.status_code == 403
    assert caught.value.retryable is False
    assert caught.value.headers == {}
    assert SECRET not in str(caught.value)
    assert "read" not in reads
    assert "closed" in reads
