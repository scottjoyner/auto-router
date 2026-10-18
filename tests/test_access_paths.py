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


def _mock_runtime_model_catalog(monkeypatch, handler):
    """Return an isolated HTTPX transport without any real network request."""
    import httpx

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "auto_router.access_paths.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 404, 429, 500, 503])
async def test_default_probe_rejects_non_200_status(monkeypatch, status):
    import httpx

    candidate = _candidate()
    _mock_runtime_model_catalog(
        monkeypatch,
        lambda _: httpx.Response(status, json={"data": [{"id": "test-model"}]},
                                 headers={"location": "http://example.invalid"} if status < 400 else {}),
    )
    selector = RuntimeAccessPathSelector([candidate.provider])
    with pytest.raises(ProviderError, match="no approved access path"):
        await selector.select(candidate)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    b'{"data":[]}',
    b'{"data":[{"id":"wrong-model"}]}',
    b'{"data":{"id":"test-model"}}',
    b'{"models":[{"id":"test-model"}]}',
    b'not-json',
    b'{"data":[{"id":"test-model"}]}' + b" " * 65537,
])
async def test_default_probe_rejects_missing_malformed_or_oversized_model(monkeypatch, body):
    import httpx

    candidate = _candidate()
    _mock_runtime_model_catalog(
        monkeypatch,
        lambda _: httpx.Response(200, content=body),
    )
    selector = RuntimeAccessPathSelector([candidate.provider])
    with pytest.raises(ProviderError, match="no approved access path"):
        await selector.select(candidate)


@pytest.mark.asyncio
async def test_default_probe_accepts_only_exact_model_on_selected_runtime(monkeypatch):
    import httpx

    candidate = _candidate()
    seen = []

    def reply(request):
        seen.append(str(request.url))
        if request.url.host == "192.168.1.44":
            return httpx.Response(200, json={"data": [{"id": "some-other-model"}]})
        return httpx.Response(200, json={"data": [{"id": "test-model"}]})

    _mock_runtime_model_catalog(monkeypatch, reply)
    choice = await RuntimeAccessPathSelector([candidate.provider]).select(candidate)
    assert choice.base_url == "http://100.90.80.70:1234/v1"
    assert seen == [
        "http://192.168.1.44:1234/v1/models",
        "http://100.90.80.70:1234/v1/models",
    ]
