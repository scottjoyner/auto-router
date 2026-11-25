import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import auto_router.quota as quota_module
from auto_router.models import ModelConfig, ProviderConfig
from auto_router.quota import InMemoryQuotaManager, RedisQuotaManager, build_quota_manager


class _FakeClock:
    """Monotonic clock that advances only when the fake sleep is called."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.now += delay


def _fake_redis_module(from_url: MagicMock):
    return SimpleNamespace(Redis=SimpleNamespace(from_url=from_url))


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
    assert quota.redis_fallback is False


def test_redis_loading_transient_recovers_to_redis_backend(monkeypatch, caplog) -> None:
    """A Redis LOADING ping failure is retried, not treated as a permanent outage."""
    loading_error = RuntimeError("LOADING Redis is loading the dataset in memory")
    client = SimpleNamespace(ping=MagicMock(return_value=True), close=MagicMock())
    from_url = MagicMock(side_effect=[loading_error, client])
    monkeypatch.setattr(quota_module, "redis_lib", _fake_redis_module(from_url))
    clock = _FakeClock()
    sleeps: list[float] = []

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock.sleep(delay)

    with caplog.at_level(logging.WARNING, logger=quota_module.logger.name):
        quota = build_quota_manager(
            "redis://redis:6379/0",
            startup_retry_seconds=10,
            sleep=sleep,
            clock=clock,
        )

    assert isinstance(quota, RedisQuotaManager)
    assert quota.client is client
    assert not quota.redis_fallback
    assert from_url.call_count == 2
    assert sleeps == [0.5]
    assert any("LOADING" in record.getMessage() for record in caplog.records)


def test_redis_permanent_failure_refuses_fallback_after_bounded_retries(monkeypatch, caplog) -> None:
    loading_error = RuntimeError("LOADING Redis is loading the dataset in memory")
    from_url = MagicMock(side_effect=loading_error)
    monkeypatch.setattr(quota_module, "redis_lib", _fake_redis_module(from_url))
    clock = _FakeClock()
    sleeps: list[float] = []

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock.sleep(delay)

    with caplog.at_level(logging.WARNING, logger=quota_module.logger.name):
        with pytest.raises(RuntimeError, match="refusing in-memory fallback") as error:
            build_quota_manager(
                "redis://redis:6379/0",
                startup_retry_seconds=2,
                sleep=sleep,
                clock=clock,
            )

    assert isinstance(error.value.__cause__, quota_module.RedisStartupError)
    assert error.value.__cause__.attempts == 4
    assert sleeps == [0.5, 1.0, 0.5]
    assert from_url.call_count == 4


def test_unexpected_startup_exception_refuses_fallback(monkeypatch) -> None:
    monkeypatch.setattr(quota_module, "redis_lib", _fake_redis_module(MagicMock()))

    def boom(*args, **kwargs):
        raise ValueError("unexpected startup failure")

    monkeypatch.setattr(quota_module, "RedisQuotaManager", boom)
    with pytest.raises(RuntimeError, match="refusing in-memory fallback") as error:
        build_quota_manager("redis://redis:6379/0")
    assert isinstance(error.value.__cause__, ValueError)


def test_configured_redis_requires_client(monkeypatch) -> None:
    monkeypatch.setattr(quota_module, "redis_lib", None)
    with pytest.raises(RuntimeError, match="redis client is not installed"):
        build_quota_manager("redis://redis:6379/0")
