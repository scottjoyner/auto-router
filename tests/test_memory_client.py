from __future__ import annotations

from types import SimpleNamespace

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

    context = await client.assemble(MemoryQuery(query="anything", privacy_class="internal"))

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
    metrics = client.metrics()
    assert metrics["retrievals"] == 1
    assert metrics["local_fallbacks"] == 1


@pytest.mark.asyncio
async def test_agent_completion_automatically_records_memory_feedback(tmp_path) -> None:
    store = MemoryStore(f"sqlite:///{tmp_path / 'memory.sqlite3'}")
    client = MemoryClient(store)
    request = SimpleNamespace(
        job_id="job-42",
        metadata={
            "repository": "scottjoyner/auto-router",
            "fleet_memory": {"memory_ids": []},
        },
    )
    result = SimpleNamespace(
        status="succeeded",
        worker_name="hermes",
        usage={"tokens_per_second": 12.5},
        stderr="",
    )

    await client.record_agent_job_outcome(request, result, 250, None)

    summary = store.summary()
    assert summary["outcome_events"] == 1
    assert summary["memory_assisted_outcomes"] == 0


@pytest.mark.asyncio
async def test_local_only_outcome_is_not_sent_to_public_remote(tmp_path, monkeypatch) -> None:
    store = MemoryStore(f"sqlite:///{tmp_path / 'memory.sqlite3'}")
    client = MemoryClient(store, base_url="https://memory.example.com")
    called = False

    async def unexpected_post(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("public remote must not receive local-only outcomes")

    monkeypatch.setattr(httpx.AsyncClient, "post", unexpected_post)
    request = SimpleNamespace(
        job_id="job-private",
        metadata={"repository": "private/repo", "fleet_memory": {"memory_ids": []}},
    )
    result = SimpleNamespace(status="succeeded", worker_name="hermes", usage={}, stderr="")

    await client.record_agent_job_outcome(request, result, 100, None)

    assert called is False
    assert store.summary()["outcome_events"] == 1
