"""Lightweight reachability checks used to gate live-service tests.

The checks only open a TCP socket: they never issue an HTTP request or contact
a model provider, so a missing service is detected quickly and safely.
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

LIVE_TESTS_ENV = "AUTO_ROUTER_LIVE_TESTS"


def service_reachable(url: str, timeout: float = 0.5) -> bool:
    """Return True when the host/port of ``url`` accepts a TCP connection."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host or not 0 < port < 65536:
            return False
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def live_tests_enabled() -> bool:
    """Explicit opt-in that runs live tests even when reachability is unknown."""
    return os.getenv(LIVE_TESTS_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def missing_services(urls: dict[str, str], timeout: float = 0.5) -> list[str]:
    """Return the names of services whose host/port is not reachable."""
    return [name for name, url in urls.items() if not service_reachable(url, timeout=timeout)]
