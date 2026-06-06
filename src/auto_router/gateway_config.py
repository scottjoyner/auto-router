"""Gateway configuration loaded from environment variables."""

from dataclasses import dataclass
import os
from urllib.parse import urlparse, urlunparse


@dataclass(frozen=True)
class GatewayConfig:
    """Configuration for agentgateway sidecar integration.
    
    Loaded from environment variables with sensible defaults.
    All fields are frozen (immutable) after creation.
    """
    
    enabled: bool
    mode: str
    base_url: str
    openai_base_url: str
    timeout_seconds: float
    fail_open_to_direct: bool
    fail_closed_for_private: bool
    emit_headers: bool
    pass_metadata_in_body: bool
    reconcile_usage: bool
    
    @classmethod
    def from_env(cls) -> "GatewayConfig":
        """Load configuration from environment variables.
        
        Returns:
            GatewayConfig instance with values from env or defaults.
        """
        return cls(
            enabled=os.getenv("AUTO_ROUTER_AGENTGATEWAY_ENABLED", "false").lower() == "true",
            mode=os.getenv("AUTO_ROUTER_GATEWAY_MODE", "direct"),
            base_url=os.getenv("AUTO_ROUTER_AGENTGATEWAY_BASE_URL", "http://agentgateway:3000"),
            openai_base_url=os.getenv(
                "AUTO_ROUTER_AGENTGATEWAY_OPENAI_BASE_URL", 
                "http://agentgateway:3000/v1"
            ),
            timeout_seconds=float(os.getenv("AUTO_ROUTER_AGENTGATEWAY_TIMEOUT_SECONDS", "120")),
            fail_open_to_direct=os.getenv(
                "AUTO_ROUTER_AGENTGATEWAY_FAIL_OPEN_TO_DIRECT", 
                "true"
            ).lower() == "true",
            fail_closed_for_private=os.getenv(
                "AUTO_ROUTER_AGENTGATEWAY_FAIL_CLOSED_FOR_PRIVATE", 
                "true"
            ).lower() == "true",
            emit_headers=os.getenv("AUTO_ROUTER_AGENTGATEWAY_EMIT_HEADERS", "true").lower() == "true",
            pass_metadata_in_body=os.getenv(
                "AUTO_ROUTER_AGENTGATEWAY_PASS_METADATA_IN_BODY", 
                "true"
            ).lower() == "true",
            reconcile_usage=os.getenv(
                "AUTO_ROUTER_AGENTGATEWAY_RECONCILE_USAGE", 
                "true"
            ).lower() == "true",
        )
    
    @property
    def is_sidecar_mode(self) -> bool:
        """Check if gateway mode is sidecar."""
        return self.mode == "sidecar"
    
    @property
    def is_direct_mode(self) -> bool:
        """Check if gateway mode is direct (current behavior)."""
        return self.mode == "direct"
    
    @property
    def metrics_url(self) -> str:
        """Get metrics endpoint URL."""
        explicit = os.getenv("AUTO_ROUTER_AGENTGATEWAY_METRICS_URL")
        if explicit:
            return explicit
        parsed = urlparse(self.base_url)
        if parsed.scheme and parsed.hostname:
            netloc = parsed.hostname
            if parsed.username:
                auth = parsed.username
                if parsed.password:
                    auth = f"{auth}:{parsed.password}"
                netloc = f"{auth}@{netloc}"
            netloc = f"{netloc}:15020"
            return urlunparse((parsed.scheme, netloc, "/metrics", "", "", ""))
        return f"{self.base_url.rstrip('/')}/metrics"
