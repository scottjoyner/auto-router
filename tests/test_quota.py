import pytest

import auto_router.quota as quota_mod
from auto_router.models import ModelConfig, ProviderConfig
from auto_router.quota import InMemoryQuotaManager, build_quota_manager


def test_quota_reservation_respects_request_limit() -> None:
    provider = ProviderConfig(name="test", type="lmstudio", base_url="http://localhost", quota_class="local")
    model = ModelConfig(alias="m", provider_model="m", capabilities={"chat"}, quota={"rpd": 1})
    quota = InMemoryQuotaManager()
    estimate = quota.estimate(model, {"messages": [{"role": "user", "content": "hello"}]})

    assert quota.reserve(provider, model, estimate) is True
    assert quota.reserve(provider, model, estimate) is False


def test_quota_snapshot_contains_remaining() -> None:
    provider = ProviderConfig(
        name="test",
        type="lmstudio",
        base_url="http://localhost",
        quota_class="local",
        models=[ModelConfig(alias="m", provider_model="m", capabilities={"chat"}, quota={"rpd": 5})],
    )
    quota = InMemoryQuotaManager()

    snapshots = quota.snapshots([provider])

    assert snapshots[0].dimensions["rpd"]["remaining"] == 5



def test_quota_release_refunds_failed_reservation() -> None:
    provider = ProviderConfig(name="test", type="lmstudio", base_url="http://localhost", quota_class="local")
    model = ModelConfig(alias="m", provider_model="m", capabilities={"chat"}, quota={"rpd": 1})
    quota = InMemoryQuotaManager()
    estimate = quota.estimate(model, {"messages": [{"role": "user", "content": "hello"}]})

    assert quota.reserve(provider, model, estimate) is True
    quota.release(provider, model, estimate)
    assert quota.reserve(provider, model, estimate) is True



def test_build_quota_manager_falls_back_to_memory_without_redis() -> None:
    quota = build_quota_manager(None)

    assert isinstance(quota, InMemoryQuotaManager)

def test_redis_configured_without_client_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quota_mod, "redis_lib", None)
    with pytest.raises(RuntimeError, match="Redis quota backend required"):
        build_quota_manager("redis://localhost:6379/0")


def test_redis_connection_error_does_not_fallback_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    class UnavailableRedis:
        def __init__(self, redis_url: str) -> None:
            raise ConnectionError("synthetic Redis outage")

    monkeypatch.setattr(quota_mod, "redis_lib", object())
    monkeypatch.setattr(quota_mod, "RedisQuotaManager", UnavailableRedis)
    with pytest.raises(RuntimeError, match="refusing in-memory fallback"):
        build_quota_manager("redis://localhost:6379/0")


def test_redis_configured_uses_connected_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    class ConnectedRedis:
        def __init__(self, redis_url: str) -> None:
            self.redis_url = redis_url

    monkeypatch.setattr(quota_mod, "redis_lib", object())
    monkeypatch.setattr(quota_mod, "RedisQuotaManager", ConnectedRedis)
    quota = build_quota_manager("redis://localhost:6379/0")
    assert isinstance(quota, ConnectedRedis)
    assert quota.redis_url == "redis://localhost:6379/0"
