"""Fail-closed, secret-safe unit tests for opt-in llama.cpp native observation."""
from __future__ import annotations

import json

import httpx
import pytest

from auto_router.llamacpp_residency_evidence import _private_base, probe

BASE = "http://127.0.0.1:1236"


def _props(**overrides):
    return {"model_alias": "deathstar-gpu", "model_path": "/private/model-file.gguf",
            "total_slots": 4, "is_sleeping": False, **overrides}


def _client(props=None, catalog=None, props_status=200, catalog_status=200):
    def mock(request):
        if request.url.path == "/props":
            return httpx.Response(props_status, json=props if props is not None else _props())
        if request.url.path == "/v1/models":
            return httpx.Response(catalog_status, json=catalog if catalog is not None
                     else {"data": [{"id": "deathstar-gpu"}]})
        raise AssertionError("Only two read-only GETs are allowed")
    return httpx.Client(transport=httpx.MockTransport(mock), follow_redirects=False)


def test_native_runtime_confirmation_never_admits_dispatch():
    with _client() as c:
        d = probe(BASE, expected_model="deathstar-gpu", client=c)
    assert d["native_runtime_residency_candidate"] is True
    assert d["props_alias_matches_catalog"] and d["expected_model_exact_match"]
    assert d["runtime_reported_slots"] == 4
    assert d["weight_artifact_attested"] is False
    assert d["signed_runtime_admission"] == "NOT_CHECKED"
    assert d["chat_capability_attested"] is False and d["authorizes_dispatch"] is False
    assert "/private" not in json.dumps(d) and "deathstar-gpu" not in json.dumps(d)


@pytest.mark.parametrize("props,catalog", [
    (_props(model_alias="other"), None),
    (_props(model_path=""), None),
    (_props(is_sleeping=True), None),
    (_props(total_slots=0), None),
    (_props(total_slots=True), None),
    ({"error": "unknown endpoint"}, None),
    (_props(), {"data": []}),
    (_props(), {"data": [{"id": "deathstar-gpu"}, {"id": "extra"}]}),
    (_props(), {"data": [{"id": "wrong"}]}),
])
def test_inconsistent_runtime_is_not_residency(props, catalog):
    with _client(props=props, catalog=catalog) as c:
        d=probe(BASE, client=c)
    assert d["native_runtime_residency_candidate"] is False
    assert d["authorizes_dispatch"] is False


def test_wrong_configured_model_is_separate_from_resident_identity():
    with _client() as c:
        d=probe(BASE,expected_model="different-configured-model",client=c)
    assert d["native_runtime_residency_candidate"]
    assert d["expected_model_exact_match"] is False
    assert not d["authorizes_dispatch"]


@pytest.mark.parametrize("endpoint", [
    "http://example.com:1234", "http://8.8.8.8:1234",
    "http://0.0.0.0:1234", "http://[::]:1234",
    "http://100.128.0.1:1234", "http://192.0.0.1:1234",
    "http://anything:1234", "ftp://127.0.0.1:1234",
    "http://account:password@127.0.0.1:1234",
    "http://127.0.0.1:1234/unsafe", "http://127.0.0.1:1234/?token=hello",
    "http://127.0.0.1:99999", "http://localhost",
])
def test_unsafe_or_unbounded_endpoint_rejected_before_network(endpoint):
    with pytest.raises(ValueError):
        _private_base(endpoint)


def test_proxy_credentials_never_inherited_for_actual_client(monkeypatch):
    real=httpx.Client
    seen=[]
    def client_factory(**kwargs):
        seen.append(kwargs)
        def fake(request):
            assert request.method=="GET" and request.url.path in ("/props","/v1/models")
            return httpx.Response(200,json=_props() if request.url.path=="/props"
                                  else {"data":[{"id":"deathstar-gpu"}]})
        kwargs["transport"]=httpx.MockTransport(fake)
        return real(**kwargs)
    monkeypatch.setattr(httpx,"Client",client_factory)
    d=probe(BASE)
    assert seen[0]["trust_env"] is False
    assert seen[0]["follow_redirects"] is False
    assert d["native_runtime_residency_candidate"]


def test_unauthorized_or_redirecting_server_is_not_residency():
    for code in [301,302,307,401,403,404,500]:
        with _client(props_status=code) as c:
            d=probe(BASE,client=c)
        assert not d["native_runtime_residency_candidate"]


def test_large_runtime_props_is_refused():
    with _client(props={**_props(),"chat_template":"x"*(300*1024)}) as c:
        d=probe(BASE,client=c)
    assert d["props_http"]==0
    assert not d["native_runtime_residency_candidate"]


def test_false_http_200_props_error_does_not_promote_capacity():
    with _client(props={'error':'unknown endpoint'}) as c:
        result=probe(BASE,client=c)
    assert result['props_http']==200
    assert result['native_runtime_residency_candidate'] is False
    assert result['authorizes_dispatch'] is False


def test_accepts_only_explicit_private_network_addresses():
    assert _private_base('http://100.78.106.121:1236') == 'http://100.78.106.121:1236'
    assert _private_base('http://localhost:1236/v1') == 'http://localhost:1236'
    assert _private_base('http://[::1]:1236') == 'http://[::1]:1236'
