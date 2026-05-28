from auto_router.models import ModelConfig, ProviderConfig
from auto_router.quota import InMemoryQuotaManager


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
