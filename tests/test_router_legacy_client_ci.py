"""Hermetic legacy client read-only parity and redaction controls."""
from __future__ import annotations

import json
from types import SimpleNamespace
import pytest
from scripts import verify_router_legacy_client_ci as c


def env(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("AUTO_ROUTER_CANARY_ENV", "disposable-github-ci")
    monkeypatch.setenv("AUTO_ROUTER_CANARY_SYNTHETIC_ADMIN_TOKEN", c.TOKEN)


def payload(path):
    if path == "/admin/providers":
        return {"providers": [{"enabled":True,"node_id":"synthetic",
            "runtime_instance_id":"synthetic","base_url":"http://127.0.0.1:9/v1",
            "api_key_env":None,"api_key_file":None,"headers":{},"models":[]}]}
    if path == "/admin/context":
        return {"source":"test","revision":"fixture"}
    if path == "/admin/runtime-projection":
        return {"configured":False,"current":None}
    if path == "/admin/providers/effective":
        return {"schema_version":1,"kind":"EFFECTIVE_ROUTER_MEMORY_READ_ONLY",
            "provider_count":1, "state_source":"LIVE_ASGI_STATE_NOT_BOOTSTRAP_PROVIDER_FILE",
            "providers":[{"resident_model_identity_verified":False,
                "service_reachability_verified":False,"admission_granted":False,
                "effective_base_url":{"status":"PARSED_NOT_PROBED",
                    "scheme":"http","hostname":"127.0.0.1","port":9,
                    "userinfo_or_query_present":False}}],
            "no_probes_or_dispatch":True}
    raise AssertionError("no fixture for unapproved path")


def test_all_read_only_parity_without_network(monkeypatch,capsys):
    env(monkeypatch)
    seen=[]
    def fake(path,auth="none",*,body=False):
        seen.append((path,auth,body))
        if path in c.PUBLIC:
            return 200,b"{}" if body else b""
        if auth in ("none","wrong"):
            return 401,b""
        return 200,json.dumps(payload(path)).encode() if body else b""
    monkeypatch.setattr(c,"read_status",fake)
    c.main()
    out=json.loads(capsys.readouterr().out)
    assert out["verdict"]=="DISPOSABLE_LEGACY_CLIENT_READ_PARITY_PASS"
    assert out["admin_missing_wrong_denials"]==8
    assert out["authorized_legacy_admin_reads"]==4
    assert out["alternate_authorization_reads"]==2
    assert out["model_requests"]==0
    assert out["cross_repo_real_clients_executed"] is False
    assert len(seen)==16
    assert all(v in ("none","wrong","header","bearer","basic") for _,v,_ in seen)


@pytest.mark.parametrize("field,value",[
    ("enabled","true"),("models",None),("node_id",1234),
    ("runtime_instance_id",1234),("base_url","http://example.org/v1"),
    ("headers",{"Authorization":"Bearer synthetic"}),("api_key_env","LEAK"),
    ("api_key_file","/run/secrets/key"),("models",[{"alias":"real"}]),
])
def test_legacy_catalog_shape_fail_closed(field,value):
    data=payload("/admin/providers")
    data["providers"][0][field]=value
    with pytest.raises(c.ClientParityDenied):
        c.verify_legacy_provider_catalog(json.dumps(data).encode())


def test_absent_ci_sentinel_blocks_before_any_request(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS",raising=False)
    monkeypatch.delenv("AUTO_ROUTER_CANARY_ENV",raising=False)
    monkeypatch.setattr(c.urllib.request,"build_opener",
        lambda *a,**kw: (_ for _ in ()).throw(AssertionError("no request permitted")))
    with pytest.raises(c.ClientParityDenied,match="disposable GitHub CI"):
        c.main()


def test_only_fixed_paths_and_gets_are_permitted(monkeypatch):
    env(monkeypatch)
    with pytest.raises(c.ClientParityDenied,match="unapproved"):
        c.read_status("/admin/load","header")
    with pytest.raises(c.ClientParityDenied,match="unapproved"):
        c.read_status("/v1/chat/completions")
    with pytest.raises(c.ClientParityDenied,match="response content"):
        c.read_status("/admin/context",body=True)


def test_malicious_redirect_never_followed(monkeypatch):
    env(monkeypatch)
    class FakeOpen:
        def open(self, req,**kw):
            raise c.urllib.error.HTTPError(req.full_url,302,"redirect",None,None)
    monkeypatch.setattr(c.urllib.request,"build_opener",lambda *a:FakeOpen())
    assert c.read_status("/admin/context","header")== (302,b"")


def test_wrong_credentials_never_read_sensitive_response(monkeypatch):
    env(monkeypatch)
    class FakeOpen:
        def open(self,req,**kw):
            raise c.urllib.error.HTTPError(req.full_url,401,"blocked",None,None)
    monkeypatch.setattr(c.urllib.request,"build_opener",lambda *a:FakeOpen())
    assert c.read_status("/admin/providers","wrong")== (401,b"")


def test_unreadable_provider_response_denied():
    for payload in (b"",b"[]",b"{","garbage".encode()):
        with pytest.raises(c.ClientParityDenied):
            c.verify_legacy_provider_catalog(payload)


@pytest.mark.parametrize("path,auth",[
    ("/admin/providers","bearer"),
    ("/admin/providers","basic"),
    ("/admin/providers","header"),
])
def test_token_headers_only_on_synthetic_loopback(monkeypatch,path,auth):
    env(monkeypatch)
    seen=[]
    class FakeReply:
        status=200
        def __enter__(self): return self
        def __exit__(self,*args):return False
    class FakeOpen:
        def open(self,req,**kw):
            seen.append((req.full_url,req.get_method(),dict(req.header_items())))
            return FakeReply()
    monkeypatch.setattr(c.urllib.request,"build_opener",lambda *a:FakeOpen())
    assert c.read_status(path,auth)[0]==200
    assert len(seen)==1
    assert seen[0][0]==c.BASE+path
    assert seen[0][1]=="GET"
    assert any(k.lower()=="x-admin-token" for k in seen[0][2]) if auth=="header" else True


def test_client_catalog_rejects_userinfo_url():
    d=payload("/admin/providers")
    d["providers"][0]["base_url"]="http://credential@127.0.0.1:9/v1"
    # The fixture must be entirely uncredentialed even on local scratch URL.
    with pytest.raises(c.ClientParityDenied):
        c.verify_legacy_provider_catalog(json.dumps(d).encode())
