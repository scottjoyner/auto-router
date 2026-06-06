"""Unit tests for agentgateway configuration."""

import os
from unittest.mock import patch

import pytest

from auto_router.gateway_config import GatewayConfig


class TestGatewayConfig:
    """Tests for GatewayConfig dataclass and from_env()."""
    
    def test_default_values(self):
        """Test default values when env vars are unset."""
        with patch.dict(os.environ, {}, clear=True):
            config = GatewayConfig.from_env()
        
        assert config.enabled is False
        assert config.mode == "direct"
        assert config.base_url == "http://agentgateway:3000"
        assert config.openai_base_url == "http://agentgateway:3000/v1"
        assert config.timeout_seconds == 120.0
        assert config.fail_open_to_direct is True
        assert config.fail_closed_for_private is True
        assert config.emit_headers is True
        assert config.pass_metadata_in_body is True
        assert config.reconcile_usage is True
    
    def test_sidecar_mode(self):
        """Test sidecar mode configuration."""
        with patch.dict(os.environ, {
            "AUTO_ROUTER_AGENTGATEWAY_ENABLED": "true",
            "AUTO_ROUTER_GATEWAY_MODE": "sidecar",
        }, clear=True):
            config = GatewayConfig.from_env()
        
        assert config.enabled is True
        assert config.mode == "sidecar"
        assert config.is_sidecar_mode is True
        assert config.is_direct_mode is False
    
    def test_custom_urls(self):
        """Test custom base URLs."""
        with patch.dict(os.environ, {
            "AUTO_ROUTER_AGENTGATEWAY_BASE_URL": "http://custom-gateway:9000",
            "AUTO_ROUTER_AGENTGATEWAY_OPENAI_BASE_URL": "http://custom-gateway:9000/v1",
        }, clear=True):
            config = GatewayConfig.from_env()
        
        assert config.base_url == "http://custom-gateway:9000"
        assert config.openai_base_url == "http://custom-gateway:9000/v1"
    
    def test_timeout_override(self):
        """Test custom timeout configuration."""
        with patch.dict(os.environ, {
            "AUTO_ROUTER_AGENTGATEWAY_TIMEOUT_SECONDS": "300",
        }, clear=True):
            config = GatewayConfig.from_env()
        
        assert config.timeout_seconds == 300.0
    
    def test_fail_open_false(self):
        """Test fail-open to direct disabled."""
        with patch.dict(os.environ, {
            "AUTO_ROUTER_AGENTGATEWAY_FAIL_OPEN_TO_DIRECT": "false",
        }, clear=True):
            config = GatewayConfig.from_env()
        
        assert config.fail_open_to_direct is False
    
    def test_fail_closed_false(self):
        """Test fail-closed for private disabled."""
        with patch.dict(os.environ, {
            "AUTO_ROUTER_AGENTGATEWAY_FAIL_CLOSED_FOR_PRIVATE": "false",
        }, clear=True):
            config = GatewayConfig.from_env()
        
        assert config.fail_closed_for_private is False
    
    def test_headers_disabled(self):
        """Test header emission disabled."""
        with patch.dict(os.environ, {
            "AUTO_ROUTER_AGENTGATEWAY_EMIT_HEADERS": "false",
        }, clear=True):
            config = GatewayConfig.from_env()
        
        assert config.emit_headers is False
    
    def test_invalid_mode(self):
        """Test invalid mode handling."""
        with patch.dict(os.environ, {
            "AUTO_ROUTER_GATEWAY_MODE": "invalid_mode_xyz",
        }, clear=True):
            config = GatewayConfig.from_env()
        
        # Should accept any string as mode (reserved for future modes)
        assert config.mode == "invalid_mode_xyz"
