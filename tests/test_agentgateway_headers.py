"""Unit tests for agentgateway metadata headers."""

import pytest

from auto_router.gateway import (
    build_gateway_headers,
    attach_gateway_metadata,
    strip_gateway_metadata,
    is_privacy_cloud_allowed,
    should_fail_closed_for_private,
)


class TestGatewayHeaders:
    """Tests for header building and privacy enforcement."""
    
    def test_required_headers_present(self):
        """Test all required headers are emitted."""
        headers = build_gateway_headers(
            request_id="test-uuid-123",
            profile="auto/code",
            stage="refine",
            priority="high",
            privacy="cloud_allowed",
            quota_mode="balanced",
            provider_plan="agentgateway-sidecar",
            model_plan="gpt-4o",
            context_revision="assistx:2026-06-05T12:00:00Z",
        )
        
        assert "x-auto-router-request-id" in headers
        assert "x-auto-router-profile" in headers
        assert "x-auto-router-stage" in headers
        assert "x-auto-router-priority" in headers
        assert "x-auto-router-privacy" in headers
        assert "x-auto-router-quota-mode" in headers
        assert "x-auto-router-provider-plan" in headers
        assert "x-auto-router-model-plan" in headers
        assert "x-auto-router-context-revision" in headers
        assert "x-auto-router-fallback-allowed" in headers
        assert "x-auto-router-local-only" in headers
        assert "x-auto-router-cloud-allowed" in headers
        
        # Verify values
        assert headers["x-auto-router-request-id"] == "test-uuid-123"
        assert headers["x-auto-router-profile"] == "auto/code"
        assert headers["x-auto-router-privacy"] == "cloud_allowed"
    
    def test_optional_headers_present(self):
        """Test optional headers are added when provided."""
        headers = build_gateway_headers(
            request_id="test-uuid",
            profile="auto/fast",
            stage="draft",
            priority="normal",
            privacy="public",
            quota_mode="preserve",
            provider_plan="default",
            model_plan="local/llama3",
            context_revision="none",
            task_id="assistx-task-456",
            agent_run_id="agent-run-789",
            node_id="deathstar-XPS-8920",
        )
        
        assert "x-auto-router-task-id" in headers
        assert "x-auto-router-agent-run-id" in headers
        assert "x-auto-router-node-id" in headers
        
        assert headers["x-auto-router-task-id"] == "assistx-task-456"
        assert headers["x-auto-router-agent-run-id"] == "agent-run-789"
        assert headers["x-auto-router-node-id"] == "deathstar-XPS-8920"
    
    def test_privacy_flags_local_only(self):
        """Test privacy flags for local-only requests."""
        headers = build_gateway_headers(
            request_id="test",
            profile="auto/local",
            stage="draft",
            priority="local_only",
            privacy="local_only",
            quota_mode="balanced",
            provider_plan="lmstudio",
            model_plan="llama3",
            context_revision="none",
        )
        
        assert headers["x-auto-router-local-only"] == "true"
        assert headers["x-auto-router-cloud-allowed"] == "false"
    
    def test_privacy_flags_private(self):
        """Test privacy flags for private requests."""
        headers = build_gateway_headers(
            request_id="test",
            profile="auto/private",
            stage="draft",
            priority="local_only",
            privacy="private",
            quota_mode="balanced",
            provider_plan="lmstudio",
            model_plan="llama3",
            context_revision="none",
        )
        
        assert headers["x-auto-router-local-only"] == "true"
        assert headers["x-auto-router-cloud-allowed"] == "false"
    
    def test_fallback_allowed_default(self):
        """Test fallback allowed defaults to true."""
        headers = build_gateway_headers(
            request_id="test",
            profile="auto/fast",
            stage="draft",
            priority="normal",
            privacy="cloud_allowed",
            quota_mode="balanced",
            provider_plan="default",
            model_plan="gpt-4o",
            context_revision="none",
        )
        
        assert headers["x-auto-router-fallback-allowed"] == "true"


class TestBodyMetadata:
    """Tests for body metadata attachment and stripping."""
    
    def test_attach_metadata(self):
        """Test attaching gateway metadata to payload."""
        payload = {
            "model": "auto/code",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        
        updated = attach_gateway_metadata(
            payload,
            request_id="req-123",
            profile="auto/code",
            stage="refine",
            priority="high",
            privacy="cloud_allowed",
            quota_mode="balanced",
            context_revision="assistx:2026-06-05T12:00:00Z",
            task_id="assistx-task-456",
            agent_run_id="agent-run-789",
            node_id="deathstar-XPS-8920",
            fallback_allowed=True,
        )
        
        assert "auto_router" in updated
        assert updated["auto_router"]["request_id"] == "req-123"
        assert updated["auto_router"]["profile"] == "auto/code"
        assert updated["auto_router"]["privacy"] == "cloud_allowed"
        assert updated["auto_router"]["task_id"] == "assistx-task-456"
        assert updated["auto_router"]["agent_run_id"] == "agent-run-789"
        assert updated["auto_router"]["node_id"] == "deathstar-XPS-8920"
    
    def test_strip_metadata(self):
        """Test stripping gateway metadata from payload."""
        payload = {
            "model": "auto/code",
            "messages": [{"role": "user", "content": "Hello"}],
            "auto_router": {"request_id": "req-123"},
        }
        
        stripped = strip_gateway_metadata(payload)
        
        assert "auto_router" not in stripped
        assert "model" in stripped
        assert "messages" in stripped
    
    def test_original_payload_unchanged(self):
        """Test that original payload is not mutated."""
        payload = {
            "model": "auto/code",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        
        _ = attach_gateway_metadata(
            payload,
            request_id="req-123",
            profile="auto/code",
            stage="refine",
            priority="high",
            privacy="cloud_allowed",
            quota_mode="balanced",
            context_revision=None,
            fallback_allowed=True,
        )
        
        # Original should be unchanged
        assert "auto_router" not in payload


class TestPrivacyFunctions:
    """Tests for privacy helper functions."""
    
    def test_cloud_allowed_true(self):
        """Test cloud allowed detection."""
        assert is_privacy_cloud_allowed("cloud_allowed") is True
        assert is_privacy_cloud_allowed("public") is True
    
    def test_cloud_allowed_false(self):
        """Test cloud not allowed for private/local_only."""
        assert is_privacy_cloud_allowed("local_only") is False
        assert is_privacy_cloud_allowed("private") is False
    
    def test_fail_closed_for_private_true(self):
        """Test fail-closed enforcement for private requests."""
        # Should fail closed when config says so and privacy is private/local_only
        assert should_fail_closed_for_private(True, "private") is True
        assert should_fail_closed_for_private(True, "local_only") is True
    
    def test_fail_closed_for_cloud_allowed(self):
        """Test fail-closed not enforced for cloud-allowed requests."""
        # Should not fail closed when privacy allows cloud
        assert should_fail_closed_for_private(True, "cloud_allowed") is False
        assert should_fail_closed_for_private(False, "private") is False  # config overrides


class TestGatewayConfigMetricsURL:
    def test_metrics_url_defaults_to_gateway_host(self):
        from auto_router.gateway_config import GatewayConfig
        from unittest.mock import patch

        with patch.dict("os.environ", {"AUTO_ROUTER_AGENTGATEWAY_BASE_URL": "http://agentgateway:3000/v1"}, clear=False):
            config = GatewayConfig.from_env()

        assert config.metrics_url == "http://agentgateway:15020/metrics"
