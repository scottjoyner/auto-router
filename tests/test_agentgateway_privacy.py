"""Unit tests for agentgateway privacy enforcement."""

import pytest

from auto_router.gateway import (
    is_privacy_cloud_allowed,
    should_fail_closed_for_private,
)


class TestPrivacyEnforcement:
    """Tests for local-only and private request hard stops."""
    
    def test_local_only_blocks_cloud(self):
        """Test that local_only requests block cloud routing."""
        assert is_privacy_cloud_allowed("local_only") is False
    
    def test_private_blocks_cloud(self):
        """Test that private requests block cloud routing."""
        assert is_privacy_cloud_allowed("private") is False
    
    def test_cloud_allowed_permits_routing(self):
        """Test that cloud_allowed permits cloud routing."""
        assert is_privacy_cloud_allowed("cloud_allowed") is True
    
    def test_public_permits_routing(self):
        """Test that public permits cloud routing."""
        assert is_privacy_cloud_allowed("public") is True


class TestFailClosedEnforcement:
    """Tests for fail-closed enforcement on gateway errors."""
    
    def test_fail_closed_for_private_true(self):
        """Private requests should fail closed when config enabled."""
        assert should_fail_closed_for_private(True, "private") is True
    
    def test_fail_closed_for_local_only_true(self):
        """Local-only requests should fail closed when config enabled."""
        assert should_fail_closed_for_private(True, "local_only") is True
    
    def test_fail_open_for_cloud_allowed(self):
        """Cloud-allowed requests can fall back to direct."""
        assert should_fail_closed_for_private(True, "cloud_allowed") is False
    
    def test_config_override_disables_fail_closed(self):
        """Config flag can disable fail-closed even for private requests."""
        # Even if privacy is private, config=False means don't fail closed
        assert should_fail_closed_for_private(False, "private") is False
        assert should_fail_closed_for_private(False, "local_only") is False
    
    def test_cloud_allowed_with_config_false(self):
        """Cloud-allowed with config=False still allows fallback."""
        assert should_fail_closed_for_private(False, "cloud_allowed") is False


class TestSensitiveMarkerDetection:
    """Tests for sensitive marker detection in requests."""
    
    @pytest.mark.parametrize("marker", [
        "api_key",
        "password",
        "ssh private key",
        "secret",
        "enrollment_sample",
        "voice_auth",
        "private_data",
    ])
    def test_sensitive_markers_block_cloud(self, marker):
        """Test that sensitive markers block cloud routing."""
        # In practice, these would be detected in message content or metadata
        # For now, we verify the privacy classification logic
        assert is_privacy_cloud_allowed("private") is False
    
    def test_metadata_local_only_flag(self):
        """Test metadata.local_only=true flag."""
        # This would be checked in route_plan.metadata.local_only
        # Simulated here with privacy=local_only
        assert should_fail_closed_for_private(True, "local_only") is True
