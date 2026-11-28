"""Integration test fixtures for cross-service health checks."""

from __future__ import annotations

import os

import httpx
import pytest
from reachability import live_tests_enabled, missing_services

ASSISTX_URL = os.getenv("ASSISTX_URL", "http://localhost:8000")
ROUTER_URL = os.getenv("ROUTER_URL", "http://localhost:8088")
ASSIGN_URL = os.getenv("ASSIGN_URL", "http://localhost:8090")

SERVICE_URLS = {"assistx": ASSISTX_URL, "router": ROUTER_URL, "assign": ASSIGN_URL}


@pytest.fixture(scope="session", autouse=True)
def live_services() -> None:
    """Skip only the cross-service integration tests when services are absent.

    Set AUTO_ROUTER_LIVE_TESTS=1 to run them without a reachability probe (for
    example in CI where the services are started in a separate step).
    """
    if live_tests_enabled():
        return
    unavailable = missing_services(SERVICE_URLS)
    if unavailable:
        pytest.skip(
            "live integration services unavailable: "
            + ", ".join(f"{name} ({SERVICE_URLS[name]})" for name in unavailable)
            + "; start the compose stack or set AUTO_ROUTER_LIVE_TESTS=1"
        )


@pytest.fixture
def assistx_client() -> httpx.Client:
    return httpx.Client(base_url=ASSISTX_URL, timeout=10)


@pytest.fixture
def router_client() -> httpx.Client:
    return httpx.Client(base_url=ROUTER_URL, timeout=10)


@pytest.fixture
def assign_client() -> httpx.Client:
    return httpx.Client(base_url=ASSIGN_URL, timeout=10)
