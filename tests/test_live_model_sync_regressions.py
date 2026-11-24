from __future__ import annotations

import time
from types import SimpleNamespace

from auto_router.live_model_routes import _is_model_loaded, sync_live_models_to_providers
from auto_router.live_models import LiveModelCache, LiveModelSnapshot
from auto_router.models import ModelConfig, ProviderConfig


def test_explicit_empty_native_instances_overrides_stale_flag():
    assert not _is_model_loaded({"raw": {"loaded_instances": []}, "loaded": True})


def test_explicit_native_instances_are_strictly_typed():
    assert not _is_model_loaded({"raw": {"loaded_instances": ["bogus"]}, "loaded": True})
    assert not _is_model_loaded({"raw": {"loaded_instances": "bogus"}, "loaded": True})
    assert _is_model_loaded({"raw": {"loaded_instances": [{"identifier": "real"}]}, "loaded": False})


def test_native_state_only_remains_supported():
    assert _is_model_loaded({"source": "lmstudio_native", "raw": {"state": "loaded"},
                             "loaded_instances": [], "loaded": True})
    assert not _is_model_loaded({"source": "lmstudio_native", "raw": {"state": "unloaded"},
                                 "loaded": True})
    assert not _is_model_loaded({"loaded": "false"})
    assert not _is_model_loaded({"source": "openai_compat_fallback", "residency": "unknown",
                                 "loaded": None})


def make_state(models, records, *, expired=False, ok=True):
    provider = ProviderConfig(name="local", type="lmstudio", base_url="http://localhost:1234/v1",
                              quota_class="local", models=models)
    now = int(time.time())
    live = LiveModelCache()
    live.put(LiveModelSnapshot(provider="local", ok=ok, fetched_at=now-60,
                               expires_at=now-1 if expired else now+60, models=records))
    return SimpleNamespace(providers=SimpleNamespace(providers=[provider]), live_models=live)


def native(mid, context=8192):
    return {"id": mid, "raw": {"loaded_instances": [{"identifier": mid}]},
            "loaded": True, "context_length": context}


def test_exact_model_keeps_configured_identity_and_policy_on_set_change():
    configured = ModelConfig(alias="local/fast", provider_model="existing",
                             quantization="Q4_K_M", model_instance_id="instance-a",
                             capabilities={"chat", "json"}, context_window=16384,
                             quota={"requests": 2}, routing_roles={"worker"})
    state = make_state([configured], [native("existing"), native("new")])
    assert sync_live_models_to_providers(state) == 1
    old, new = state.providers.providers[0].models
    assert old.alias == configured.alias
    assert old.quantization == configured.quantization
    assert old.model_instance_id == configured.model_instance_id
    assert old.quota == configured.quota
    assert old.routing_roles == configured.routing_roles
    assert old.context_window == 8192
    assert not new.allow_agent_runtime and not new.allow_code_execution


def test_changed_context_updates_even_when_model_ids_unchanged():
    configured = ModelConfig(alias="local/fast", provider_model="existing", context_window=16384)
    state = make_state([configured], [native("existing", 4096)])
    assert sync_live_models_to_providers(state) == 1
    assert state.providers.providers[0].models[0].context_window == 4096
    assert sync_live_models_to_providers(state) == 0


def test_unloaded_removed_and_duplicate_model_deduplicated():
    configured = ModelConfig(alias="local/fast", provider_model="existing")
    state = make_state([configured], [dict(native("existing"), raw={"loaded_instances": []}),
                                      native("new"), native("new")])
    assert sync_live_models_to_providers(state) == 1
    assert [m.provider_model for m in state.providers.providers[0].models] == ["new"]


def test_stale_snapshot_cannot_add_new_models():
    configured = ModelConfig(alias="local/fast", provider_model="existing")
    state = make_state([configured], [native("new")], expired=True)
    assert sync_live_models_to_providers(state) == 0
    assert state.providers.providers[0].models == [configured]


def test_malformed_metadata_does_not_block_valid_records():
    state = make_state([], [dict(native("first"), metadata={"context_length": "bad"}),
                            native("second", 8192)])
    assert sync_live_models_to_providers(state) == 1
    assert [m.provider_model for m in state.providers.providers[0].models] == ["first", "second"]



def test_signature_failure_does_not_suppress_fresh_observations(monkeypatch):
    import asyncio
    import auto_router.live_model_routes as routes

    class Registry:
        def __init__(self):
            self.writes = 0
        def _snapshot_signature(self, models):
            raise ValueError("unavailable")
        def latest_for_provider(self, provider):
            return None
        def save_snapshot(self, snapshot):
            self.writes += 1
            raise OSError("read-only")

    async def fetch(provider):
        return [{"id": "new"}]

    monkeypatch.setattr(routes, "fetch_provider_models", fetch)
    monkeypatch.setattr(routes, "_LIVE_OK_STATE", {})
    monkeypatch.setattr(routes, "_LIVE_SIG_STATE", {})
    registry = Registry()
    provider = ProviderConfig(name="signature-test", type="lmstudio",
                              base_url="http://localhost:1234/v1", quota_class="local")
    state = SimpleNamespace(live_models=LiveModelCache(), model_registry=registry)
    for _ in range(2):
        records = asyncio.run(routes.refresh_provider_models(state, [provider]))
        assert records[0]["ok"] is True
        assert records[0]["registry_error"] == "read-only"
    assert registry.writes == 2
