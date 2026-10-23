"""No-network tests for GitHub's disposable full-ASGI auth and redaction probe."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts import verify_admin_canary_ci as c

def _safe_payload():
    return json.dumps({
        "schema_version":1,
        "kind":"EFFECTIVE_ROUTER_MEMORY_READ_ONLY",
        "state_source":"LIVE_ASGI_STATE_NOT_BOOTSTRAP_PROVIDER_FILE",
        "provider_count":1,
        "no_probes_or_dispatch":True,
        "providers":[{
            "name":"fixture","provider_id":"fixture-id",
            "resident_model_identity_verified":False,
            "service_reachability_verified":False,
            "admission_granted":False,
            "effective_base_url":{"status":"PARSED_NOT_PROBED","hostname":"fixture.invalid",
                                  "scheme":"http","port":1234,"userinfo_or_query_present":False},
        }],
    }).encode()

def test_valid_synthetic_snapshot_only():
    assert c.assert_redacted(_safe_payload())=={"provider_count":1,"sanitized":True}

@pytest.mark.parametrize("key,val",[
    ("headers",{"Authorization":"Bearer SECRET"}),
    ("api_key_env","MY_PRIVATE_KEY"),
    ("base_url","http://user:secret@fixture:1234"),
    ("password","not-for-output"),
])
def test_forbidden_raw_fields_rejected(key,val):
    doc=json.loads(_safe_payload())
    doc["providers"][0][key]=val
    with pytest.raises(c.CanaryRejected):
        c.assert_redacted(json.dumps(doc).encode())

@pytest.mark.parametrize("flag",[
    "resident_model_identity_verified","service_reachability_verified","admission_granted"])
def test_unverified_runtime_flags_must_be_false(flag):
    doc=json.loads(_safe_payload())
    doc["providers"][0][flag]=True
    with pytest.raises(c.CanaryRejected):
        c.assert_redacted(json.dumps(doc).encode())

def test_absent_disposable_ci_sentinel_denies(monkeypatch):
    monkeypatch.delenv("AUTO_ROUTER_CANARY_ENV",raising=False)
    monkeypatch.setenv("AUTO_ROUTER_CANARY_SYNTHETIC_ADMIN_TOKEN","smoke-admin-token")
    monkeypatch.setattr(c,"get_status",lambda *_a,**_kw:(_ for _ in ()).throw(
        AssertionError("network must not be touched")))
    with pytest.raises(c.CanaryRejected,match="disposable GitHub CI"):
        c.main()

def test_fake_full_canary_passes_without_network(monkeypatch,capsys):
    monkeypatch.setenv("AUTO_ROUTER_CANARY_ENV","disposable-github-ci")
    monkeypatch.setenv("AUTO_ROUTER_CANARY_SYNTHETIC_ADMIN_TOKEN","smoke-admin-token")
    paths=[]
    def fake(path,token=None,*,read_body=False):
        paths.append((path,token,read_body))
        if token in (None,"known-incorrect-ci-token"):
            return 401,b""
        return 200,_safe_payload() if read_body else b""
    monkeypatch.setattr(c,"get_status",fake)
    c.main()
    printed=capsys.readouterr().out
    doc=json.loads(printed)
    assert doc["result"]=="DISPOSABLE_FULL_ASGI_ADMIN_AUTH_PASS"
    assert doc["checked_routes"]==10
    assert "smoke-admin-token" not in printed
    assert len(paths)==23

def test_if_any_anonymous_admin_returns_200_deny(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_CANARY_ENV","disposable-github-ci")
    monkeypatch.setenv("AUTO_ROUTER_CANARY_SYNTHETIC_ADMIN_TOKEN","smoke-admin-token")
    monkeypatch.setattr(c,"get_status",lambda *_a,**_kw:(200,b""))
    with pytest.raises(c.CanaryRejected,match="authentication bypass"):
        c.main()

def test_only_allowlisted_get_paths():
    with pytest.raises(c.CanaryRejected):
        c.get_status("/admin/circuits/reset")
