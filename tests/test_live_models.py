import pytest

from auto_router.live_models import LiveModelCache
from auto_router.models import ProviderConfig
from auto_router.providers import normalize_model_record


def test_normalize_model_record_keeps_raw_payload() -> None:
    payload = {"id": "gpt-oss-120b", "object": "model", "owned_by": "cerebras", "extra": "value"}

    normalized = normalize_model_record(payload)

    assert normalized["id"] == "gpt-oss-120b"
    assert normalized["object"] == "model"
    assert normalized["owned_by"] == "cerebras"
    assert normalized["raw"] == payload


@pytest.mark.asyncio
async def test_live_model_cache_reuses_fresh_snapshot() -> None:
    cache = LiveModelCache(ttl_seconds=60)
    provider = ProviderConfig(name="cerebras", type="openai_compatible", base_url="https://example.test/v1")
    calls = 0

    async def fetcher(_: ProviderConfig):
        nonlocal calls
        calls += 1
        return [{"id": "gpt-oss-120b"}]

    first = await cache.get_or_refresh(provider, fetcher)
    second = await cache.get_or_refresh(provider, fetcher)

    assert first is second
    assert calls == 1
    assert cache.snapshot()[0]["model_count"] == 1


@pytest.mark.asyncio
async def test_live_model_cache_records_fetch_errors() -> None:
    cache = LiveModelCache(ttl_seconds=60)
    provider = ProviderConfig(name="cerebras", type="openai_compatible", base_url="https://example.test/v1")

    async def fetcher(_: ProviderConfig):
        raise RuntimeError("boom")

    snapshot = await cache.refresh_provider(provider, fetcher)

    assert snapshot.ok is False
    assert "boom" in (snapshot.error or "")
    assert cache.snapshot()[0]["ok"] is False
