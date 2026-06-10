"""Provider mock tests using pytest-httpx."""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from auto_router.models import ModelConfig, ProviderConfig, RouterRequest
from auto_router.providers import (
    OpenAICompatibleProvider,
    ProviderError,
    ProviderHealth,
    ProviderResponse,
    build_provider,
)


@pytest.fixture
def lmstudio_config() -> ProviderConfig:
    return ProviderConfig(
        name="lmstudio",
        type="lmstudio",
        base_url="http://localhost:1234/v1",
        quota_class="local",
        models=[
            ModelConfig(alias="local/model", provider_model="local-model", capabilities={"chat"}),
        ],
    )


@pytest.fixture
def cloud_config() -> ProviderConfig:
    return ProviderConfig(
        name="groq",
        type="openai_compatible",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        quota_class="fast_free",
        models=[
            ModelConfig(alias="groq/fast", provider_model="llama-3.1-8b", capabilities={"chat"}),
        ],
    )


@pytest.fixture
def router_request() -> RouterRequest:
    return RouterRequest(
        request_id="test-1",
        route="chat_completions",
        messages=[{"role": "user", "content": "hello"}],
        raw_body={
            "model": "local/model",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 100,
        },
    )


class TestHealth:
    async def test_health_ok(self, lmstudio_config: ProviderConfig, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url="http://localhost:1234/v1/models", status_code=200)
        provider = OpenAICompatibleProvider(lmstudio_config)
        result = await provider.health()
        assert isinstance(result, ProviderHealth)
        assert result.ok is True

    async def test_health_5xx(self, lmstudio_config: ProviderConfig, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url="http://localhost:1234/v1/models", status_code=503)
        provider = OpenAICompatibleProvider(lmstudio_config)
        result = await provider.health()
        assert result.ok is False

    async def test_health_connection_error(self, lmstudio_config: ProviderConfig, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_exception(httpx.ConnectError("connection refused"))
        provider = OpenAICompatibleProvider(lmstudio_config)
        result = await provider.health()
        assert result.ok is False

    async def test_health_missing_api_key(self, cloud_config: ProviderConfig) -> None:
        provider = OpenAICompatibleProvider(cloud_config)
        result = await provider.health()
        assert result.ok is False
        assert "missing API key" in result.detail


class TestListModels:
    async def test_list_models_returns_normalized(self, lmstudio_config: ProviderConfig, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://localhost:1234/v1/models",
            json={"data": [{"id": "local-model", "object": "model", "owned_by": "lmstudio"}]},
        )
        provider = OpenAICompatibleProvider(lmstudio_config)
        models = await provider.list_models()
        assert len(models) == 1
        assert models[0]["id"] == "local-model"
        assert models[0]["owned_by"] == "lmstudio"

    async def test_list_models_http_error(self, lmstudio_config: ProviderConfig, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url="http://localhost:1234/v1/models", status_code=429, headers={"Retry-After": "30"})
        provider = OpenAICompatibleProvider(lmstudio_config)
        with pytest.raises(ProviderError) as exc_info:
            await provider.list_models()
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after_seconds == 30

    async def test_list_models_not_configured(self, cloud_config: ProviderConfig) -> None:
        provider = OpenAICompatibleProvider(cloud_config)
        with pytest.raises(ProviderError, match="not configured"):
            await provider.list_models()


class TestChatCompletions:
    async def test_chat_completion_ok(self, lmstudio_config: ProviderConfig, router_request: RouterRequest, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://localhost:1234/v1/chat/completions",
            json={
                "id": "chat-1",
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )
        provider = OpenAICompatibleProvider(lmstudio_config)
        result = await provider.chat_completions(router_request, "local-model")
        assert isinstance(result, ProviderResponse)
        assert result.provider == "lmstudio"
        assert result.model == "local-model"
        assert result.usage["total_tokens"] == 15
        assert result.data["id"] == "chat-1"

    async def test_chat_completion_4xx(self, lmstudio_config: ProviderConfig, router_request: RouterRequest, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url="http://localhost:1234/v1/chat/completions", status_code=400, json={"error": "bad request"})
        provider = OpenAICompatibleProvider(lmstudio_config)
        with pytest.raises(ProviderError) as exc_info:
            await provider.chat_completions(router_request, "local-model")
        assert exc_info.value.retryable is False

    async def test_chat_completion_5xx(self, lmstudio_config: ProviderConfig, router_request: RouterRequest, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url="http://localhost:1234/v1/chat/completions", status_code=502)
        provider = OpenAICompatibleProvider(lmstudio_config)
        with pytest.raises(ProviderError) as exc_info:
            await provider.chat_completions(router_request, "local-model")
        assert exc_info.value.retryable is True

    async def test_chat_completion_not_configured(self, cloud_config: ProviderConfig, router_request: RouterRequest) -> None:
        provider = OpenAICompatibleProvider(cloud_config)
        with pytest.raises(ProviderError, match="not configured"):
            await provider.chat_completions(router_request, "llama-3.1-8b")


class TestEmbeddings:
    async def test_embeddings_ok(self, lmstudio_config: ProviderConfig, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://localhost:1234/v1/embeddings",
            json={"data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}], "usage": {"prompt_tokens": 4, "total_tokens": 4}},
        )
        request = RouterRequest(request_id="emb-1", route="embeddings", input="hello", raw_body={"input": "hello"})
        provider = OpenAICompatibleProvider(lmstudio_config)
        result = await provider.embeddings(request, "local-model")
        assert result.provider == "lmstudio"
        assert result.data["data"][0]["index"] == 0


class TestStreaming:
    async def test_stream_chat_completions(self, lmstudio_config: ProviderConfig, router_request: RouterRequest, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://localhost:1234/v1/chat/completions",
            status_code=200,
            headers={"content-type": "text/event-stream"},
            content=b"data: {\"choices\": [{\"delta\": {\"content\": \"hi\"}}]}\n\ndata: [DONE]\n\n",
        )
        provider = OpenAICompatibleProvider(lmstudio_config)
        result = await provider.stream_chat_completions(router_request, "local-model")
        assert result.provider == "lmstudio"
        assert result.status_code == 200

    async def test_stream_error_4xx(self, lmstudio_config: ProviderConfig, router_request: RouterRequest, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url="http://localhost:1234/v1/chat/completions", status_code=429, headers={"Retry-After": "10"})
        provider = OpenAICompatibleProvider(lmstudio_config)
        with pytest.raises(ProviderError) as exc_info:
            await provider.stream_chat_completions(router_request, "local-model")
        assert exc_info.value.retry_after_seconds == 10


class TestIsConfigured:
    def test_lmstudio_always_configured(self, lmstudio_config: ProviderConfig) -> None:
        provider = OpenAICompatibleProvider(lmstudio_config)
        assert provider.is_configured() is True

    def test_cloud_configured_with_key(self, cloud_config: ProviderConfig) -> None:
        os.environ["GROQ_API_KEY"] = "sk-test"
        provider = OpenAICompatibleProvider(cloud_config)
        assert provider.is_configured() is True
        del os.environ["GROQ_API_KEY"]

    def test_cloud_not_configured_without_key(self, cloud_config: ProviderConfig) -> None:
        if "GROQ_API_KEY" in os.environ:
            del os.environ["GROQ_API_KEY"]
        provider = OpenAICompatibleProvider(cloud_config)
        assert provider.is_configured() is False


class TestBuildProvider:
    def test_build_lmstudio(self, lmstudio_config: ProviderConfig) -> None:
        provider = build_provider(lmstudio_config)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.name == "lmstudio"

    def test_build_openai_compatible(self, cloud_config: ProviderConfig) -> None:
        provider = build_provider(cloud_config)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.name == "groq"


class TestHeaders:
    def test_headers_include_api_key(self, lmstudio_config: ProviderConfig) -> None:
        provider = OpenAICompatibleProvider(lmstudio_config)
        headers = provider._headers()
        assert headers["Content-Type"] == "application/json"

    def test_headers_authorization(self, cloud_config: ProviderConfig) -> None:
        os.environ["GROQ_API_KEY"] = "sk-secret"
        provider = OpenAICompatibleProvider(cloud_config)
        headers = provider._headers()
        assert headers["Authorization"] == "Bearer sk-secret"
        del os.environ["GROQ_API_KEY"]
