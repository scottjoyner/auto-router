"""Synthetic status-only positive auth coverage; no Docker, network, real tokens."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.admin_positive_auth_ci_canary import (
    FIXTURE_TOKEN,WRONG_TOKEN,SAFE_PATHS,check,request_status,
)

class FakeResponse:
    def __init__(self,status):
        self.status=status

    def read(self,*args,**kwargs):
        raise AssertionError("reading any admin response body forbidden")


class FakeConnection:
    calls=[]
    def __init__(self,host,port,timeout):
        assert (host,port,timeout)==("127.0.0.1",8088,2)
        self.path=None
        self.headers=None

    def request(self,method,path,headers):
        assert method=="GET"
        assert path in SAFE_PATHS
        assert "Authorization" not in headers
        assert set(headers)<={"Accept","User-Agent","Connection","X-Admin-Token"}
        self.path=path
        self.headers=headers.copy()
        self.calls.append((method,path,headers.get("X-Admin-Token")))

    def getresponse(self):
        token=self.headers.get("X-Admin-Token")
        return FakeResponse(200 if token==FIXTURE_TOKEN else 401)

    def close(self):
        pass


def test_real_http_client_protocol_calls_only_15_safe_readonly_gets():
    FakeConnection.calls.clear()
    receipt=check(status_request=lambda port,path,token: request_status(
        port,path,token,connection_factory=FakeConnection))
    assert receipt["gate"]=="PASS_CI_FIXTURE_ONLY"
    assert len(FakeConnection.calls)==15
    assert set(p for _,p,_ in FakeConnection.calls)==set(SAFE_PATHS)
    assert "/admin/providers/health" not in SAFE_PATHS
    assert all(method=="GET" for method,_,_ in FakeConnection.calls)
    assert all(not key in json.dumps(receipt) for key in (FIXTURE_TOKEN,WRONG_TOKEN))
    assert receipt["production_credential_used"] is False
    assert receipt["response_bodies_read"] is False
    assert receipt["production_ready"] is False


@pytest.mark.parametrize(("token","expected"),[
    ("",401),(WRONG_TOKEN,401),(FIXTURE_TOKEN,200),
])
def test_status_only_client_does_not_consume_body(token,expected):
    FakeConnection.calls.clear()
    assert request_status(8088,"/admin/providers",token,
                          connection_factory=FakeConnection)==expected
    assert len(FakeConnection.calls)==1


@pytest.mark.parametrize("path",["/admin/providers/health","/v1/models","/admin/circuits/reset"])
def test_unsafe_admin_routes_are_not_probed(path):
    with pytest.raises(ValueError):
        request_status(8088,path,FIXTURE_TOKEN,
                       connection_factory=lambda *_a,**_kw:None)


@pytest.mark.parametrize("port",[80,8089,0,False,"8088"])
def test_unapproved_ports_are_denied(port):
    with pytest.raises(ValueError):
        request_status(port,"/admin/providers",FIXTURE_TOKEN,
                       connection_factory=lambda *_a,**_kw:None)


@pytest.mark.parametrize("token",["actual-operator-token","Bearer fake","admin"])
def test_unrecognized_credentials_are_never_accepted(token):
    with pytest.raises(ValueError):
        request_status(8088,"/admin/providers",token,
                       connection_factory=lambda *_a,**_kw:None)


def test_failed_invalid_token_or_positive_status_holds():
    def deny_all(port,path,token):
        return 401
    denied=check(status_request=deny_all)
    assert denied["gate"]=="HOLD"
    assert sum(not x["pass"] for x in denied["checks"])==5
    def accepts_anything(port,path,token):
        return 200
    leaked=check(status_request=accepts_anything)
    assert leaked["gate"]=="HOLD"
    assert sum(not x["pass"] for x in leaked["checks"])==10


def test_one_missing_connection_denies_all_clear():
    receipt=check(status_request=lambda p,path,t:None if path==SAFE_PATHS[0] else
                  (200 if t==FIXTURE_TOKEN else 401))
    assert receipt["gate"]=="HOLD"
    assert sum(not row["pass"] for row in receipt["checks"])==3


def test_wrong_hosted_environment_fails_without_contacting_any_port():
    root=Path(__file__).resolve().parents[1]
    env={k:v for k,v in os.environ.items() if k not in ("CI","GITHUB_ACTIONS","PYTHONPATH","PYTHONHOME")}
    process=subprocess.run([sys.executable,"-B","-m","scripts.admin_positive_auth_ci_canary",
                            "--explicit-ci-synthetic-only"],
                           cwd=root,env=env,
                           capture_output=True,text=True,check=False,timeout=4)
    assert process.returncode==2
    assert json.loads(process.stdout)["reason"]=="not_hosted_ci"
    assert "Traceback" not in process.stderr


def test_missing_explicit_ci_optin_fails():
    root=Path(__file__).resolve().parents[1]
    process=subprocess.run([sys.executable,"-B","-m","scripts.admin_positive_auth_ci_canary"],
                           cwd=root,capture_output=True,text=True,check=False,timeout=4)
    assert process.returncode!=0
    assert "explicit-ci-synthetic-only" in process.stderr
