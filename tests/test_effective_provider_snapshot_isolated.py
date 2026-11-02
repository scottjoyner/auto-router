"""Isolated tests for selectively backported PR41 pure provider snapshot.

Exercises actual source endpoint + actual shared admin dependency with synthetic
settings and registry. Does not initialize Router, Docker, or network services.
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from test_current_admin_dependency_isolated import _real_admin_dependency

ROOT=Path(__file__).resolve().parents[1]
MAIN=ROOT/"src/auto_router/main.py"
SNAPSHOT=ROOT/"src/auto_router/effective_provider_snapshot.py"

def _snapshot():
    spec=importlib.util.spec_from_file_location("isolated_effective_snapshot",SNAPSHOT)
    assert spec and spec.loader
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def _test_client():
    mod=_snapshot()
    provider=SimpleNamespace(
        id="synthetic-runtime",name="resident-example",type="lmstudio",node_id="fake-node",
        enabled=True,
        base_url="https://admin:PRIVATE_PASSWORD@synthetic.invalid:443/v1?access=TOKEN",
        access_urls=["http://user:ACCESS_SECRET@synthetic.invalid:1234/v1?bearer=TOKEN"],
        api_key_env="SECRET_API_KEY_ENV",headers={"Authorization":"Bearer HIDDEN"},
        models=[SimpleNamespace(alias="synthetic-model",provider_model="test-model")],
    )
    state=SimpleNamespace(
        providers=SimpleNamespace(providers=[provider]),
        runtime_projection_manager=SimpleNamespace(current=None),
    )
    token="strict-synthetic-test-only"
    dependency=_real_admin_dependency(SimpleNamespace(admin_token=token))
    source=ast.parse(MAIN.read_text())
    fn=next(n for n in source.body if isinstance(n,ast.AsyncFunctionDef)
            and n.name=="admin_effective_providers")
    assert any(isinstance(d,ast.Call) and d.args
               and isinstance(d.args[0],ast.Constant)
               and d.args[0].value=="/admin/providers/effective"
               for d in fn.decorator_list)
    isolated=ast.Module(body=[fn],type_ignores=[])
    ast.fix_missing_locations(isolated)
    app=FastAPI()
    scope={"app":app,"Depends":Depends,"require_admin":dependency,
           "effective_snapshot":mod.effective_snapshot,"state":state,"Any":Any}
    exec(compile(isolated,str(MAIN),"exec"),scope)
    return TestClient(app,raise_server_exceptions=False),token,mod,state

def test_get_effective_rejects_missing_and_wrong_credentials():
    client,token,_,_=_test_client()
    assert client.get("/admin/providers/effective").status_code==401
    assert client.get("/admin/providers/effective",headers={"X-Admin-Token":"wrong"}).status_code==401
    client.close()

def test_authenticated_effective_snapshot_contains_no_secrets():
    client,token,_,_=_test_client()
    response=client.get("/admin/providers/effective",headers={"X-Admin-Token":token})
    assert response.status_code==200
    payload=response.text
    for secret in ("PRIVATE_PASSWORD","ACCESS_SECRET","TOKEN","SECRET_API_KEY_ENV","HIDDEN","Bearer","/v1?"):
        assert secret not in payload
    record=response.json()["providers"][0]
    assert record["name"]=="resident-example"
    assert record["effective_base_url"]["hostname"]=="synthetic.invalid"
    assert record["effective_base_url"]["port"]==443
    assert record["resident_model_identity_verified"] is False
    assert record["admission_granted"] is False
    client.close()

def test_snapshot_is_deterministic_at_fixed_clock():
    _,_,module,state=_test_client()
    a=module.effective_snapshot(state,observed_at_ms=1800000000000)
    b=module.effective_snapshot(state,observed_at_ms=1800000000000)
    assert a==b
    assert a["no_probes_or_dispatch"] is True
    assert len(a["snapshot_sha256"])==64

def test_nonexistent_or_oversized_provider_registry_fails_closed():
    mod=_snapshot()
    for registry in (None,SimpleNamespace(providers=[None]*1001)):
        s=SimpleNamespace(providers=registry)
        try:mod.effective_snapshot(s,observed_at_ms=1800000000000)
        except ValueError:pass
        else:raise AssertionError("invalid provider registry was accepted")

def test_url_never_emits_query_userinfo_or_fragment():
    mod=_snapshot()
    item=mod.endpoint_reference("https://admin:SECRET@host.invalid:8123/v1?token=HIDDEN#fragment")
    text=json.dumps(item)
    assert "SECRET" not in text
    assert "HIDDEN" not in text
    assert "/v1" not in text
    assert item["hostname"]=="host.invalid"
    assert item["port"]==8123
    assert item["userinfo_or_query_present"] is True
