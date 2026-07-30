import pytest

from auto_router.access_paths import RuntimeAccessPathSelector, classify_access_transport
from auto_router.models import ModelConfig, ProviderCandidate, ProviderConfig
from auto_router.providers import ProviderError


def _candidate() -> ProviderCandidate:
    provider = ProviderConfig(
        name="runtime-xwing",
        type="lmstudio",
        node_id="xwing",
        runtime_instance_id="lmstudio-xwing-1234",
        parallel_slots=1,
        queue_limit=2,
        queue_timeout_seconds=10,
        base_url="http://192.168.1.44:1234/v1",
        access_urls=[
            "http://192.168.1.44:1234/v1",
            "http://100.90.80.70:1234/v1",
        ],
        quota_class="local",
        models=[
            ModelConfig(
                alias="local/reconciliation-default",
                provider_model="test-model",
                capabilities={"chat"},
            )
        ],
    )
    return ProviderCandidate(provider=provider, model=provider.models[0])


@pytest.mark.asyncio
async def test_prefers_reachable_lan_path() -> None:
    candidate = _candidate()
    probes: list[str] = []

    async def probe(url: str) -> bool:
        probes.append(url)
        return True

    selector = RuntimeAccessPathSelector(
        [candidate.provider],
        probe=probe,
        cache_ttl_seconds=30,
    )

    choice = await selector.select(candidate)

    assert choice.base_url == "http://192.168.1.44:1234/v1"
    assert choice.transport == "lan"
    assert probes == ["http://192.168.1.44:1234/v1"]


@pytest.mark.asyncio
async def test_falls_back_to_tailscale_for_same_runtime() -> None:
    candidate = _candidate()
    probes: list[str] = []

    async def probe(url: str) -> bool:
        probes.append(url)
        return url.startswith("http://100.")

    selector = RuntimeAccessPathSelector(
        [candidate.provider],
        probe=probe,
        cache_ttl_seconds=30,
    )

    choice = await selector.select(candidate)

    assert choice.runtime_instance_id == "lmstudio-xwing-1234"
    assert choice.base_url == "http://100.90.80.70:1234/v1"
    assert choice.transport == "tailscale"
    assert probes == [
        "http://192.168.1.44:1234/v1",
        "http://100.90.80.70:1234/v1",
    ]


@pytest.mark.asyncio
async def test_selected_path_is_cached_without_new_discovery() -> None:
    candidate = _candidate()
    calls = 0

    async def probe(_: str) -> bool:
        nonlocal calls
        calls += 1
        return True

    selector = RuntimeAccessPathSelector(
        [candidate.provider],
        probe=probe,
        cache_ttl_seconds=30,
    )

    first = await selector.select(candidate)
    second = await selector.select(candidate)

    assert first == second
    assert calls == 1


@pytest.mark.asyncio
async def test_all_unreachable_paths_fail_closed() -> None:
    candidate = _candidate()

    async def probe(_: str) -> bool:
        return False

    selector = RuntimeAccessPathSelector([candidate.provider], probe=probe)

    with pytest.raises(ProviderError, match="no approved access path") as exc:
        await selector.select(candidate)

    assert exc.value.status_code == 503
    snapshot = selector.snapshot()[0]
    assert snapshot["selected_access_url"] is None
    assert all(value == 1 for value in snapshot["probe_failures"].values())


def test_transport_classification_distinguishes_lan_and_tailnet() -> None:
    assert classify_access_transport("http://192.168.1.44:1234/v1") == "lan"
    assert classify_access_transport("http://100.90.80.70:1234/v1") == "tailscale"
    assert classify_access_transport("http://xwing.example.ts.net:1234/v1") == "tailscale"
