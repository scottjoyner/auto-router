"""HTTP tests for actual current require_admin code in an isolated synthetic FastAPI.

The dependency is extracted from the pinned source AST; no production Router
module, settings, Docker images, network, configuration or fleet state is
loaded. This does not replace a rebuilt deployed-image auth canary.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
SECURITY=ROOT/"src"/"auto_router"/"security.py"

def _real_admin_dependency(fake_settings):
    tree=ast.parse(SECURITY.read_text())
    names={"_unauthorized","require_admin"}
    funcs=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))
           and n.name in names]
    assert {n.name for n in funcs}==names
    # Preserve the original body and signature, replacing only settings source.
    module=ast.Module(body=funcs,type_ignores=[])
    ast.fix_missing_locations(module)
    import secrets
    from fastapi import Header, HTTPException, Request, status
    from fastapi.security import (
        HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials
    )
    scope={"secrets":secrets,"Depends":Depends,"Header":Header,"HTTPException":HTTPException,
           "Request":Request,"status":status,"HTTPBasicCredentials":HTTPBasicCredentials,
           "HTTPAuthorizationCredentials":HTTPAuthorizationCredentials,
           "_basic":HTTPBasic(auto_error=False),
           "_bearer":HTTPBearer(auto_error=False),
           "get_settings":lambda:fake_settings}
    exec(compile(module,str(SECURITY),"exec"),scope)
    return scope["require_admin"]

def _app(admin_token: str):
    settings=SimpleNamespace(admin_token=admin_token)
    require_admin=_real_admin_dependency(settings)
    app=FastAPI()
    counter={"calls":0}

    @app.get("/admin/providers")
    async def providers(_=Depends(require_admin)):
        return {"providers":[{"name":"synthetic"}]}

    @app.post("/admin/circuits/reset")
    async def reset(_=Depends(require_admin)):
        counter["calls"]+=1
        return {"cleared":True}

    return TestClient(app),counter

@pytest.mark.parametrize("value",["","synthetic-admin-secret"])
def test_missing_credentials_never_reach_admin_handler(value):
    client,counter=_app(value)
    assert client.get("/admin/providers").status_code==401
    assert client.post("/admin/circuits/reset").status_code==401
    assert counter["calls"]==0
    client.close()

@pytest.mark.parametrize("header",[
    {"X-Admin-Token":"wrong"},
    {"Authorization":"Bearer wrong"},
    {"Authorization":"Basic YWRtaW46d3Jvbmc="},
])
def test_wrong_credentials_denied(header):
    client,counter=_app("synthetic-admin-secret")
    assert client.get("/admin/providers",headers=header).status_code==401
    assert client.post("/admin/circuits/reset",headers=header).status_code==401
    assert counter["calls"]==0
    client.close()

def test_valid_header_allows_only_synthetic_handler():
    client,counter=_app("synthetic-admin-secret")
    headers={"X-Admin-Token":"synthetic-admin-secret"}
    assert client.get("/admin/providers",headers=headers).status_code==200
    assert client.post("/admin/circuits/reset",headers=headers).status_code==200
    assert counter["calls"]==1
    client.close()

def test_valid_bearer_allows_synthetic_read():
    client,counter=_app("synthetic-admin-secret")
    assert client.get("/admin/providers",headers={
        "Authorization":"Bearer synthetic-admin-secret"}).status_code==200
    assert counter["calls"]==0
    client.close()
