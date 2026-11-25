import asyncio
from types import SimpleNamespace

import auto_router.live_model_routes as routes
from auto_router.models import ProviderConfig, ModelConfig

def p(name="local", node="node-a", url="http://node-a:1234/v1", **kw):
    return ProviderConfig(name=name,type="lmstudio",node_id=node,base_url=url,**kw)

def merge(monkeypatch, current, discovered):
    async def discover(*args,**kw):
        return discovered
    monkeypatch.setattr(routes,"discovered_lmstudio_providers",discover)
    state=SimpleNamespace(providers=SimpleNamespace(providers=current))
    changed=asyncio.run(routes.merge_discovered_lmstudio_providers(state))
    return changed,state.providers.providers

def test_discovery_cannot_reenable_disabled_provider(monkeypatch):
    configured=p(enabled=False)
    assert merge(monkeypatch,[configured],[p()])==(0,[configured])

def test_discovery_cannot_move_configured_endpoint_or_identity(monkeypatch):
    configured=p(api_key_env="SYNTHETIC_ONLY",priority=100,parallel_slots=2,
        allow_agent_runtime=True,models=[ModelConfig(alias="approved",provider_model="m")])
    observed=p(node="different",url="http://other:1234/v1",priority=1)
    assert merge(monkeypatch,[configured],[observed])==(0,[configured])

def test_discovery_cannot_reintroduce_disabled_node_as_alias(monkeypatch):
    configured=p(enabled=False)
    observed=p(name="discovered-alias",url="http://node-a-new:1234/v1")
    assert merge(monkeypatch,[configured],[observed])==(0,[configured])

def test_discovery_cannot_reintroduce_disabled_endpoint_as_alias(monkeypatch):
    configured=p(enabled=False)
    observed=p(name="discovered-alias",node="different")
    assert merge(monkeypatch,[configured],[observed])==(0,[configured])

def test_new_unconfigured_provider_still_discovered(monkeypatch):
    configured=p(enabled=False)
    observed=p(name="new",node="node-b",url="http://node-b:1234/v1")
    assert merge(monkeypatch,[configured],[observed])==(1,[configured,observed])

def test_existing_configured_provider_no_spurious_change(monkeypatch):
    configured=p()
    assert merge(monkeypatch,[configured],[p()])==(0,[configured])
