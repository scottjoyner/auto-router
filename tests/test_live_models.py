import asyncio

from types import SimpleNamespace

from auto_router.config import ProviderRegistry
from auto_router.context import ContextSnapshot
from auto_router.live_model_routes import discovered_lmstudio_providers, probeable_providers, refresh_provider_models
from auto_router.live_models import LiveModelCache
from auto_router.model_registry import ModelRegistryStore
from auto_router.models import ProviderConfig, ProviderHealth
from auto_router.providers import normalize_model_record
from auto_router.signal_registry import ContextSignalStore


def test_normalize_model_record_keeps_raw_payload() -> None:
    payload = {"id": "gpt-oss-120b", "object": "model", "owned_by": "cerebras", "extra": "value"}

    normalized = normalize_model_record(payload)

    assert normalized["id"] == "gpt-oss-120b"
    assert normalized["object"] == "model"
    assert normalized["owned_by"] == "cerebras"
    assert normalized["raw"] == payload


def test_normalize_model_record_uses_lmstudio_key_and_publisher() -> None:
    payload = {"key": "ornith-1.0-9b", "display_name": "Ornith 1.0 9B", "publisher": "deepreinforce-ai"}

    normalized = normalize_model_record(payload)

    assert normalized["id"] == "ornith-1.0-9b"
    assert normalized["owned_by"] == "deepreinforce-ai"


def test_live_model_cache_reuses_fresh_snapshot() -> None:
    cache = LiveModelCache(ttl_seconds=60)
    provider = ProviderConfig(name="cerebras", type="openai_compatible", base_url="https://example.test/v1")
    calls = 0

    async def fetcher(_: ProviderConfig):
        nonlocal calls
        calls += 1
        return [{"id": "gpt-oss-120b"}]

    first = asyncio.run(cache.get_or_refresh(provider, fetcher))
    second = asyncio.run(cache.get_or_refresh(provider, fetcher))

    assert first is second
    assert calls == 1
    assert cache.snapshot()[0]["model_count"] == 1


def test_list_models_returns_canonical_ids(monkeypatch) -> None:
    providers = ProviderRegistry.model_validate(
        {
            "providers": [
                {
                    "name": "Cerebras API",
                    "type": "openai_compatible",
                    "enabled": True,
                    "base_url": "https://cerebras.example/v1",
                    "priority": 10,
                    "models": [
                        {
                            "alias": "cerebras/flash-reasoner",
                            "provider_model": "gpt-oss-120b",
                            "capabilities": ["chat", "low_latency"],
                        }
                    ],
                }
            ]
        }
    )
    state = SimpleNamespace(providers=providers, context=ContextSnapshot())

    from auto_router import main as main_module

    monkeypatch.setattr(main_module, "state", state)
    payload = asyncio.run(main_module.list_models())

    assert payload["object"] == "list"
    assert payload["data"][0]["id"] == "cerebras api.gpt-oss-120b"
    assert payload["data"][0]["owned_by"] == "cerebras api"
    assert payload["data"][0]["provider_model"] == "gpt-oss-120b"


def test_refresh_provider_models_probes_all_enabled_providers_and_projects_context(tmp_path) -> None:
    providers = ProviderRegistry.model_validate(
        {
            "providers": [
                {
                    "name": "cerebras",
                    "type": "openai_compatible",
                    "enabled": True,
                    "base_url": "https://cerebras.example/v1",
                    "priority": 10,
                },
                {
                    "name": "lmstudio-local",
                    "type": "lmstudio",
                    "enabled": True,
                    "base_url": "http://localhost:1234/v1",
                    "priority": 20,
                },
            ]
        }
    )
    state = SimpleNamespace(
        providers=providers,
        context=ContextSnapshot(),
        live_models=LiveModelCache(ttl_seconds=60),
        model_registry=ModelRegistryStore(f"sqlite:///{tmp_path / 'router.sqlite3'}"),
        signal_registry=ContextSignalStore(f"sqlite:///{tmp_path / 'signals.sqlite3'}"),
        policy_engine=SimpleNamespace(context=None),
    )
    calls: list[str] = []

    async def fake_fetch(provider: ProviderConfig):
        calls.append(provider.name)
        if provider.name == "cerebras":
            return [{"id": "gpt-oss-120b"}, {"id": "zai-glm-4.7"}]
        return [{"id": "local-llama"}]

    from auto_router import live_model_routes as routes

    original_fetch = routes.fetch_provider_models
    routes.fetch_provider_models = fake_fetch
    try:
        records = asyncio.run(refresh_provider_models(state, probeable_providers(state.providers.providers)))
    finally:
        routes.fetch_provider_models = original_fetch

    assert calls == ["cerebras", "lmstudio-local"]
    assert {record["provider"] for record in records} == {"cerebras", "lmstudio-local"}
    assert records[0]["ok"] is True
    assert records[0]["model_count"] == 2
    assert records[0]["probe"]["drift"] is False
    assert state.model_registry.summary()["providers"] == 2
    assert state.context.model_for("gpt-oss-120b") is not None
    assert state.context.model_for("local-llama") is not None
    assert state.context.all_models()
    assert state.context.signal_summary()["total"] == 3
    assert state.context.signal_summary()["model"] == 3
    assert state.signal_registry.summary()["signals"] == 3


def test_discovered_lmstudio_providers_include_context_services(monkeypatch) -> None:
    providers = ProviderRegistry.model_validate(
        {
            "providers": [
                {
                    "name": "cerebras",
                    "type": "openai_compatible",
                    "enabled": True,
                    "base_url": "https://cerebras.example/v1",
                    "priority": 10,
                }
            ]
        }
    )
    context = ContextSnapshot.model_validate(
        {
            "services": [
                {
                    "service_id": "tailnet.r2d2.lmstudio",
                    "name": "r2d2 LMStudio",
                    "url": "http://r2d2.tailcb8954.ts.net:1234/v1",
                    "health_url": "http://r2d2.tailcb8954.ts.net:1234/v1/models",
                    "service_type": "lmstudio",
                    "provider": "lmstudio-r2d2",
                    "node_id": "r2d2",
                }
            ]
        }
    )
    state = SimpleNamespace(providers=providers, context=context)
    monkeypatch.setattr("auto_router.live_model_routes.discover_tailnet_lmstudio_services", lambda: [])

    discovered = discovered_lmstudio_providers(state)

    assert len(discovered) == 1
    assert discovered[0].name == "lmstudio-r2d2"
    assert discovered[0].base_url == "http://r2d2.tailcb8954.ts.net:1234/v1"
    assert discovered[0].type == "lmstudio"


def test_refresh_provider_models_replaces_stale_bootstrap_model_on_changed_node(tmp_path) -> None:
    providers = ProviderRegistry.model_validate(
        {
            "providers": [
                {
                    "name": "lmstudio-joyner",
                    "type": "lmstudio",
                    "enabled": True,
                    "base_url": "http://joyner.tailcb8954.ts.net:1234/v1",
                    "priority": 900,
                    "quota_class": "local",
                    "node_id": "joyner",
                    "models": [],
                }
            ]
        }
    )
    context = ContextSnapshot.model_validate(
        {
            "providers": [
                {
                    "provider": "lmstudio-joyner",
                    "lane": "local",
                    "local": True,
                    "can_use_free_api": False,
                    "blocked": False,
                    "node_id": "joyner",
                    "aliases": ["local/tailnet-joyner"],
                    "detail": "Tailnet LM Studio node",
                    "models": [
                        {
                            "model_id": "lmstudio-joyner.orinth-1.0-9b",
                            "name": "orinth-1.0-9b",
                            "provider": "lmstudio-joyner",
                            "provider_model": "orinth-1.0-9b",
                            "lane": "local",
                            "local": True,
                            "can_use_free_api": False,
                            "blocked": False,
                            "node_id": "joyner",
                            "aliases": ["local/orinth-1.0-9b-joyner"],
                            "capabilities": ["chat", "local_only"],
                            "context_window": 8192,
                            "quota": {},
                            "detail": "stale bootstrap model",
                            "priority": 900,
                        }
                    ],
                }
            ]
        }
    )
    state = SimpleNamespace(
        providers=providers,
        context=context,
        live_models=LiveModelCache(ttl_seconds=60),
        model_registry=ModelRegistryStore(f"sqlite:///{tmp_path / 'router.sqlite3'}"),
        signal_registry=ContextSignalStore(f"sqlite:///{tmp_path / 'signals.sqlite3'}"),
        policy_engine=SimpleNamespace(context=None),
    )

    async def fake_fetch(provider: ProviderConfig):
        assert provider.name == "lmstudio-joyner"
        return [{"id": "qwen2.5-14b"}]

    from auto_router import live_model_routes as routes

    original_fetch = routes.fetch_provider_models
    routes.fetch_provider_models = fake_fetch
    try:
        asyncio.run(refresh_provider_models(state, probeable_providers(state.providers.providers)))
    finally:
        routes.fetch_provider_models = original_fetch

    assert state.context.model_for("orinth-1.0-9b") is None
    refreshed = state.context.model_for("qwen2.5-14b")
    assert refreshed is not None
    assert refreshed.provider_model == "qwen2.5-14b"
    assert refreshed.provider == "lmstudio-joyner"
    assert refreshed.node_id == "joyner"


def test_live_model_cache_records_fetch_errors() -> None:
    cache = LiveModelCache(ttl_seconds=60)
    provider = ProviderConfig(name="cerebras", type="openai_compatible", base_url="https://example.test/v1")

    async def fetcher(_: ProviderConfig):
        raise RuntimeError("boom")

    snapshot = asyncio.run(cache.refresh_provider(provider, fetcher))

    assert snapshot.ok is False
    assert "boom" in (snapshot.error or "")
    assert cache.snapshot()[0]["ok"] is False


def test_provider_health_reports_emit_provider_signals(tmp_path, monkeypatch) -> None:
    providers = ProviderRegistry.model_validate(
        {
            "providers": [
                {
                    "name": "cerebras",
                    "type": "openai_compatible",
                    "enabled": True,
                    "base_url": "https://cerebras.example/v1",
                    "priority": 10,
                    "node_id": "r2d2",
                }
            ]
        }
    )
    state = SimpleNamespace(
        providers=providers,
        circuits=SimpleNamespace(snapshot=lambda: []),
        context=ContextSnapshot(),
        signal_registry=ContextSignalStore(f"sqlite:///{tmp_path / 'signals.sqlite3'}"),
        policy_engine=SimpleNamespace(context=None),
    )

    class FakeAdapter:
        async def health(self) -> ProviderHealth:
            return ProviderHealth(provider="cerebras", ok=True, detail="HTTP 200")

    monkeypatch.setattr("auto_router.main.build_provider", lambda provider, timeout_seconds: FakeAdapter())

    from auto_router import main as main_module
    from auto_router.main import _provider_health_reports

    monkeypatch.setattr(main_module, "state", state)
    reports = asyncio.run(_provider_health_reports())

    assert reports[0]["ok"] is True
    assert state.context.signal_summary()["provider"] == 1
    assert state.context.signal_summary()["node"] == 1
    assert state.signal_registry.summary()["provider"] == 1
    assert state.signal_registry.summary()["node"] == 1
