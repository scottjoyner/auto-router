from __future__ import annotations

import httpx
import pytest

from auto_router.memory_client import MemoryClient
from auto_router.memory_models import MemoryQuery
from auto_router.memory_store import MemoryStore


@pytest.mark.asyncio
async def test_remote_failure_degrades_to_local_store(tmp_path, monkeypatch) -> None:
    proxy_names = (
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    )
    for name in proxy_names:
        monkeypatch.delenv(name, raising=False)
    store = MemoryStore(f"sqlite:///{tmp_path / 'memory.sqlite3'}")
    client = MemoryClient(store, base_url="http://memory.invalid", timeout_seconds=0.1)

    async def fail_post(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "post", fail_post)

    context = await client.assemble(
        MemoryQuery(query="anything", privacy_class="internal")
    )

    assert context.degraded is True
    assert context.backend == "sqlite-lexical"
    assert any("Remote memory lookup failed" in warning for warning in context.warnings)


@pytest.mark.asyncio
async def test_local_only_query_never_reaches_public_remote(tmp_path, monkeypatch) -> None:
    store = MemoryStore(f"sqlite:///{tmp_path / 'memory.sqlite3'}")
    client = MemoryClient(store, base_url="https://memory.example.com")
    called = False

    async def unexpected_post(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("public remote must not receive local_only query")

    monkeypatch.setattr(httpx.AsyncClient, "post", unexpected_post)

    context = await client.assemble(
        MemoryQuery(query="private repository task", privacy_class="local_only")
    )

    assert called is False
    assert any("non-private service URL" in warning for warning in context.warnings)
