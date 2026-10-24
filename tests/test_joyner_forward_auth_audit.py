"""No real network I/O in tests; synthetic Joyner ingress classification."""
from __future__ import annotations

import pytest

from scripts import joyner_forward_auth_audit as m


LAN = "192.168.1.79"
TAIL = "100.83.215.83"
ADDRS = {(e.host, e.port) for e in m.endpoints(LAN, TAIL)}


def test_parse_ss_only_exact_local_listeners():
    sample = """
LISTEN 0 512 127.0.0.1:1234 0.0.0.0:*
LISTEN 0 5 192.168.1.79:1234 0.0.0.0:*
LISTEN 0 5 100.83.215.83:1234 0.0.0.0:*
LISTEN 0 512 100.83.215.83:18321 0.0.0.0:*
LISTEN 0 512 [::]:1234 [::]:*
ESTAB 0 0 192.168.1.79:1236 192.168.1.79:40404
"""
    parsed = m.listener_addresses(sample)
    assert ("127.0.0.1", 1234) in parsed
    assert (LAN, 1234) in parsed
    assert (TAIL, 18321) in parsed
    assert ("::", 1234) not in parsed
    assert (LAN, 1236) not in parsed


def test_no_key_200_distinguished_from_401_without_routing_approval():
    def statuses(endpoint, timeout):
        if endpoint.port in (18320, 18321):
            return 401, None
        return 200, None

    data = m.audit(LAN, TAIL, probe=statuses,
                   observed_listeners=ADDRS, node_hostname="joyner")
    assert data["status"] == "PERIMETER_NO_GO"
    assert len(data["unauthenticated_model_catalogs"]) == 4
    assert len(data["missing_credential_rejected"]) == 2
    assert data["approved_for_routing"] is False
    assert data["credential_sent"] is False
    assert data["external_reachability_tested"] is False
    assert all(row["approved_for_dispatch"] is False for row in data["endpoints"])


def test_http_403_is_negative_auth_witness_but_not_full_approval():
    data = m.audit(LAN, TAIL, probe=lambda e,t: (403, None),
                   observed_listeners=ADDRS, node_hostname="joyner")
    assert len(data["missing_credential_rejected"]) == 6
    assert data["unauthenticated_model_catalogs"] == []
    assert not data["approved_for_routing"]


def test_http_200_without_expected_listener_is_not_misreported_as_bound():
    data = m.audit(LAN, TAIL, probe=lambda e,t: (200, None),
                   observed_listeners={(TAIL, 18321)}, node_hostname="joyner")
    assert data["unauthenticated_model_catalogs"] == ["toolcall-tailnet-guarded"]
    assert all(r["listener_bound_on_joyner"] is False
               for r in data["endpoints"] if r["port"] in (1234, 1236))
    assert not data["approved_for_routing"]


def test_wrong_observer_host_never_claims_reachability_or_probes():
    def fail(*args):
        raise AssertionError("no probe should occur from x1")
    data = m.audit(LAN, TAIL, probe=fail,
                   observed_listeners=ADDRS, node_hostname="x1-370")
    assert data["observer_host_matches"] is False
    assert data["unauthenticated_model_catalogs"] == []
    assert all(r["models_no_credential_http"] is None for r in data["endpoints"])
    assert all(r["listener_bound_on_joyner"] is None for r in data["endpoints"])


@pytest.mark.parametrize("input_ip", [
    "localhost", "127.0.0.1/path", "http://127.0.0.1", "192.168.1.79:80",
    "bad.address", "::1", "100.0.0.1/31",
])
def test_refuses_nonliteral_or_nonipv4_addresses(input_ip):
    with pytest.raises(ValueError):
        m.audit(input_ip, TAIL, observed_listeners=ADDRS, node_hostname="joyner")


def test_model_catalog_error_is_unknown_not_guarded():
    data = m.audit(LAN, TAIL, probe=lambda e,t: (None,"ConnectionRefusedError"),
                   observed_listeners=ADDRS, node_hostname="joyner")
    assert data["missing_credential_rejected"] == []
    assert data["unauthenticated_model_catalogs"] == []
    assert all(x["classification"] == "UNVERIFIED" for x in data["endpoints"])
    assert not data["approved_for_training"]


def test_other_http_responses_do_not_count_as_auth():
    data = m.audit(LAN, TAIL, probe=lambda e,t: (404,None),
                   observed_listeners=ADDRS, node_hostname="joyner")
    assert data["missing_credential_rejected"] == []
    assert all(x["classification"] == "UNEXPECTED_HTTP_RESPONSE"
               for x in data["endpoints"])


def test_missing_ss_visibility_is_not_an_exposure_claim(monkeypatch):
    monkeypatch.setattr(m, "read_listeners", lambda: (set(),"ss_failed"))
    data = m.audit(LAN, TAIL, probe=lambda e,t: (200,None),
                   node_hostname="joyner")
    assert data["listener_enumeration_error"] == "ss_failed"
    assert data["unauthenticated_model_catalogs"] == []
    assert all(x["listener_bound_on_joyner"] is False for x in data["endpoints"])


def test_no_invocation_of_generation_or_secret_post(monkeypatch):
    calls = []
    class FakeResp:
        status = 401
        def __enter__(self): return self
        def __exit__(self,*args): return False
    class FakeOpener:
        def open(self, request, timeout):
            calls.append((request.get_method(),request.full_url,request.header_items(),timeout))
            return FakeResp()
    monkeypatch.setattr(m, "build_opener", lambda *args: FakeOpener())
    status,err=m.read_models_status(m.Endpoint("a",TAIL,18320,"Tailnet"),2)
    assert status==401 and err is None
    method,url,headers,timeout=calls[0]
    assert method=="GET"
    assert url.endswith("/v1/models")
    assert not any(k.lower()=="authorization" for k,v in headers)
    assert timeout==2


def test_exit_code_remains_denied_even_when_everything_401(monkeypatch,capsys):
    import sys,json
    monkeypatch.setattr(m, "audit", lambda *a,**k: {
        "status":"PERIMETER_NO_GO","approved_for_routing":False,
        "unauthenticated_model_catalogs":[]})
    monkeypatch.setattr(sys, "argv", ["joyner_forward_auth_audit.py"])
    assert m.main()==78
    data=json.loads(capsys.readouterr().out)
    assert data["approved_for_routing"] is False
