"""Offline tests proving the canary is read-only and truly fails closed."""
from unittest.mock import patch

import pytest

from scripts.admin_auth_readonly_canary import ADMIN_PATHS, probe


class FakeResponse:
    def __init__(self,status):
        self.status=status
    def read(self,*args,**kwargs):
        raise AssertionError("canary must never read any response body")


class FakeConnection:
    responses=[]
    calls=[]
    def __init__(self,host,port,timeout):
        assert host=="127.0.0.1"
        assert timeout==2
    def request(self,method,path,headers):
        assert method=="GET"
        assert path in ADMIN_PATHS
        assert "Authorization" not in headers
        assert "X-Admin-Token" not in headers
        self.calls.append(path)
    def getresponse(self):
        return FakeResponse(self.responses.pop(0))
    def close(self):
        pass


def test_all_401_statuses_are_required():
    FakeConnection.calls=[]
    FakeConnection.responses=[401]*len(ADMIN_PATHS)
    with patch("scripts.admin_auth_readonly_canary.http.client.HTTPConnection",FakeConnection):
        result=probe(8088)
    assert result["all_denied"] is True
    assert result["response_bodies_read"] is False
    assert FakeConnection.calls==list(ADMIN_PATHS)


def test_any_anonymous_200_denies_cutover():
    FakeConnection.calls=[]
    FakeConnection.responses=[401,200]+[401]*(len(ADMIN_PATHS)-2)
    with patch("scripts.admin_auth_readonly_canary.http.client.HTTPConnection",FakeConnection):
        result=probe(8088)
    assert result["all_denied"] is False
    assert [row["path"] for row in result["results"] if not row["anonymous_denied"]]==[
        "/admin/context"
    ]


def test_invalid_ports_and_mutating_paths_rejected():
    for port in [0,65536,"8088",True]:
        with pytest.raises(ValueError):
            probe(port)
    with pytest.raises(ValueError):
        probe(8088,("/admin/circuits/reset",))
    with pytest.raises(ValueError):
        probe(8088,())
