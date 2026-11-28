from __future__ import annotations

import asyncio
import inspect

import pytest
from reachability import live_tests_enabled


@pytest.fixture(autouse=True)
def fresh_settings_cache():
    """Settings are lru_cached; do not leak env overrides between tests."""
    from auto_router.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def live_provider_inventory() -> None:
    """Gate assertions about the live tailnet provider model inventory.

    Those model IDs can only be verified against a running provider endpoint;
    opt in with AUTO_ROUTER_LIVE_TESTS=1 when the fleet is available.
    """
    if not live_tests_enabled():
        pytest.skip(
            "live provider model inventory required; "
            "set AUTO_ROUTER_LIVE_TESTS=1 when the tailnet fleet is reachable"
        )


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: run async test functions with asyncio")


def pytest_pyfunc_call(pyfuncitem):
    if "asyncio" not in pyfuncitem.keywords:
        return None
    testfunction = pyfuncitem.obj
    if not inspect.iscoroutinefunction(testfunction):
        return None
    funcargs = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
    asyncio.run(testfunction(**funcargs))
    return True
