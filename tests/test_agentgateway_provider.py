"""Unit tests for agentgateway provider adapter."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from auto_router.gateway_config import GatewayConfig
from auto_router.models import ProviderConfig, RouterRequest
from auto_router.providers import AgentGatewayProviderAdapter, ProviderError


class TestAgentGatewayProviderAdapter:
    """Tests for AgentGatewayProviderAdapter class."""

    @pytest.fixture
    def adapter(self):
        """Create a test adapter instance."""
        config = ProviderConfig(
            id="agentgateway-sidecar",
            name="agentgateway sidecar",
            type="openai_compatible",
            base_url="http://agentgateway:3000/v1",
            enabled=True,
        )
        return AgentGatewayProviderAdapter(config, timeout_seconds=120.0)

    @pytest.fixture
    def gateway_config(self):
        """Create a test gateway config."""
        return GatewayConfig(
            enabled=True,
            mode="sidecar",
            base_url="http://agentgateway:3000",
            openai_base_url="http://agentgateway:3000/v1",
            timeout_seconds=120.0,
            fail_open_to_direct=True,
            fail_closed_for_private=False,
            emit_headers=True,
            pass_metadata_in_body=True,
            reconcile_usage=True,
        )

    @pytest.fixture
    def sample_request(self):
        """Create a test router request."""
        return RouterRequest(
            request_id="test-req-123",
            route="chat_completions",
            model="auto/code",
            messages=[{"role": "user", "content": "Hello, world!"}],
            raw_body={
                "model": "auto/code",
                "messages": [{"role": "user", "content": "Hello, world!"}],
            },
        )

    def test_chat_completions_success(self, adapter, gateway_config, sample_request):
        """Test successful chat completion through gateway."""
        mock_response = SimpleNamespace(
            status_code=200,
            json=Mock(return_value={
                "id": "chat-123",
                "choices": [{"message": {"content": "Hello!"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }),
            elapsed=SimpleNamespace(total_seconds=Mock(return_value=0.5)),
            text="",
        )

        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_response)):
            result = asyncio.run(
                adapter.chat_completions(
                    sample_request,
                    "gpt-4o",
                    gateway_config=gateway_config,
                )
            )

        assert result.provider == "agentgateway-sidecar"
        assert result.model == "gpt-4o"
        assert result.status_code == 200
        assert result.usage["total_tokens"] == 15
        assert "_gateway_metadata" in result.data

    def test_chat_completions_with_headers(self, adapter, gateway_config, sample_request):
        """Test that headers are sent with request."""
        mock_response = SimpleNamespace(
            status_code=200,
            json=Mock(return_value={
                "choices": [{"message": {"content": "OK"}}],
                "usage": {},
            }),
            elapsed=SimpleNamespace(total_seconds=Mock(return_value=0.1)),
            text="",
        )

        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_response)) as mock_post:
            asyncio.run(
                adapter.chat_completions(
                    sample_request,
                    "gpt-4o",
                    gateway_config=gateway_config,
                )
            )

        call_kwargs = mock_post.call_args[1]
        assert "headers" in call_kwargs
        headers = call_kwargs["headers"]
        assert "x-auto-router-request-id" in headers
        assert "x-auto-router-profile" in headers

    def test_chat_completions_gateway_error(self, adapter, gateway_config, sample_request):
        """Test handling of gateway HTTP errors."""
        mock_response = SimpleNamespace(
            status_code=502,
            text="Bad Gateway",
            headers={},
        )

        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_response)):
            with pytest.raises(ProviderError) as exc_info:
                asyncio.run(
                    adapter.chat_completions(
                        sample_request,
                        "gpt-4o",
                        gateway_config=gateway_config,
                    )
                )

        assert "agentgateway returned HTTP 502" in str(exc_info.value)

    def test_chat_completions_uses_route_context(self, adapter, gateway_config, sample_request):
        mock_response = SimpleNamespace(
            status_code=200,
            json=Mock(return_value={
                "choices": [{"message": {"content": "OK"}}],
                "usage": {},
            }),
            elapsed=SimpleNamespace(total_seconds=Mock(return_value=0.1)),
            text="",
        )

        route_plan = type(
            "RoutePlan",
            (),
            {
                "profile": "high_priority_deliverable",
                "stage": "judge",
                "priority": "repo_critical",
                "privacy": "private",
                "quota_mode": "preserve",
            },
        )()

        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_response)) as mock_post:
            asyncio.run(
                adapter.chat_completions(
                    sample_request,
                    "gpt-4o",
                    gateway_config=gateway_config,
                    route_plan=route_plan,
                )
            )

        headers = mock_post.call_args[1]["headers"]
        assert headers["x-auto-router-profile"] == "high_priority_deliverable"
        assert headers["x-auto-router-stage"] == "judge"
        assert headers["x-auto-router-priority"] == "repo_critical"
        assert headers["x-auto-router-privacy"] == "private"
        payload = mock_post.call_args[1]["json"]
        assert payload["auto_router"]["profile"] == "high_priority_deliverable"
        assert payload["auto_router"]["stage"] == "judge"
        assert payload["auto_router"]["priority"] == "repo_critical"

    def test_chat_completions_network_error(self, adapter, gateway_config, sample_request):
        """Test handling of network errors."""
        import httpx

        with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.ConnectError("Connection refused"))):
            with pytest.raises(ProviderError) as exc_info:
                asyncio.run(
                    adapter.chat_completions(
                        sample_request,
                        "gpt-4o",
                        gateway_config=gateway_config,
                    )
                )

        assert "agentgateway request failed" in str(exc_info.value)


class TestGatewayMetadataInResponse:
    """Tests for gateway metadata stored in responses."""

    @pytest.fixture
    def adapter(self):
        config = ProviderConfig(
            id="agentgateway-sidecar",
            name="agentgateway sidecar",
            type="openai_compatible",
            base_url="http://agentgateway:3000/v1",
            enabled=True,
        )
        return AgentGatewayProviderAdapter(config, timeout_seconds=120.0)

    @pytest.fixture
    def gateway_config(self):
        return GatewayConfig(
            enabled=True,
            mode="sidecar",
            base_url="http://agentgateway:3000",
            openai_base_url="http://agentgateway:3000/v1",
            timeout_seconds=120.0,
            fail_open_to_direct=True,
            fail_closed_for_private=False,
            emit_headers=True,
            pass_metadata_in_body=True,
            reconcile_usage=True,
        )

    @pytest.fixture
    def sample_request(self):
        return RouterRequest(
            request_id="test-req-123",
            route="chat_completions",
            model="auto/code",
            messages=[{"role": "user", "content": "Hello, world!"}],
            raw_body={
                "model": "auto/code",
                "messages": [{"role": "user", "content": "Hello, world!"}],
            },
        )

    def test_gateway_metadata_included(self, adapter, gateway_config, sample_request):
        """Test that response includes gateway metadata for ledger."""
        mock_response = SimpleNamespace(
            status_code=200,
            json=Mock(return_value={
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"total_tokens": 10},
            }),
            elapsed=SimpleNamespace(total_seconds=Mock(return_value=0.5)),
            text="",
        )

        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_response)):
            result = asyncio.run(
                adapter.chat_completions(
                    sample_request,
                    "gpt-4o",
                    gateway_config=gateway_config,
                )
            )

        assert "_gateway_metadata" in result.data
        metadata = result.data["_gateway_metadata"]
        assert "request_id" in metadata
        assert "provider" in metadata
        assert "latency_ms" in metadata
