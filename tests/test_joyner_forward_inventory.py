"""Offline denial, privacy and exact-port tests for unadmitted Joyner forwards."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    assert spec and spec.loader
    import sys
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


census = module("joyner_forward_consumer_census")
auth = module("joyner_forward_auth_audit")
cutover = module("joyner_cutover_readiness")


def ss(*links):
    return "\n".join(
        f"ESTAB 0 0 {local} {peer}" for local, peer in links
    )


class Clock:
    def __init__(self):
        self.t = 0.0

    def clock(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_bounded_empty_census_never_grants_cutover():
    clock = Clock()
    result = census.census(
        duration=0.3, interval=0.1, hostname="joyner", reader=lambda: "",
        clock=clock.clock, sleeper=clock.sleep,
    )
    assert result["status"] == "BOUNDED_SAMPLE_COMPLETE_NOT_CUSTODY_PROOF"
    assert result["samples"] == 4
    assert result["unique_clients_observed"] == 0
    assert result["client_inventory_complete"] is False
    assert result["may_disable_forward"] is False
    assert result["may_promote_router"] is False


def test_census_deduplicates_clients_without_leaking_address():
    snapshot = ss(
        ("192.168.1.79:1234", "192.168.1.44:57233"),
        ("100.83.215.83:1236", "100.116.11.34:59999"),
        ("192.168.1.79:1234", "192.168.1.44:57012"),
    )
    clock = Clock()
    result = census.census(
        duration=0.2, interval=0.1, hostname="joyner", reader=lambda: snapshot,
        clock=clock.clock, sleeper=clock.sleep,
    )
    assert result["unique_clients_observed"] == 2
    assert result["routes"]["minicpm_lan"]["unique_client_ips_seen"] == 1
    assert result["routes"]["ling_tailnet"]["unique_client_ips_seen"] == 1
    assert result["routes"]["minicpm_lan"]["samples_with_connections"] == 3
    assert "192.168.1.44" not in json.dumps(result)
    assert "100.116.11.34" not in json.dumps(result)
    assert "client_tags" not in json.dumps(result)


def test_census_rejects_wrong_host_without_calling_reader():
    def fail():
        raise AssertionError("unexpected kernel read")
    result = census.census(duration=1, interval=0.5, hostname="xwing", reader=fail)
    assert result["status"] == "OBSERVER_NOT_JOYNER"
    assert result["may_disable_forward"] is False


def test_census_read_failure_is_explicit():
    clock = Clock()
    def broken():
        raise OSError("connection metadata denied")
    result = census.census(
        duration=.2, interval=.1, hostname="joyner", reader=broken,
        clock=clock.clock, sleeper=clock.sleep)
    assert result["status"] == "OBSERVATIONS_INCOMPLETE"
    assert result["socket_read_errors"] == result["samples"]
    assert result["client_inventory_complete"] is False


def test_census_rejects_invalid_port_or_addresses():
    for text in ("[::1]:1234", "192.168.1.79:0", "localhost:1234",
                 "192.168.1.79:65536", "192.168.1.79:not-a-port"):
        assert census.address(text) is None
    assert census.parse_ss(ss(("192.168.1.79:1234", "127.0.0.1:4444"))) == set()


def test_census_bounded_duration_and_sample_cadence():
    for duration, interval in ((61,.5), (0,.5), (1,.09), (1,6)):
        with pytest.raises(ValueError):
            census.census(duration=duration,interval=interval,hostname="joyner")


def test_auth_probe_no_credentials_and_no_implicit_dispatch():
    endpoints=auth.endpoints("192.168.1.79","100.83.215.83")
    listener={(e.host,e.port) for e in endpoints}
    statuses={1234:200,1236:200,18320:401,18321:401}
    result=auth.audit("192.168.1.79","100.83.215.83",
        observed_listeners=listener,node_hostname="joyner",
        probe=lambda endpoint, timeout:(statuses[endpoint.port],None))
    assert result["status"]=="PERIMETER_NO_GO"
    assert result["approved_for_routing"] is False
    assert result["credential_sent"] is False
    assert result["model_generation_performed"] is False
    assert len(result["unauthenticated_model_catalogs"]) == 4
    assert len(result["missing_credential_rejected"]) == 2


def test_auth_audit_wrong_host_never_probes():
    def forbidden(*args):
        raise AssertionError("attempted live IO")
    result=auth.audit("192.168.1.79","100.83.215.83",
        observed_listeners=set(),node_hostname="other",probe=forbidden)
    assert result["approved_for_routing"] is False
    assert all(item["classification"]=="UNVERIFIED" for item in result["endpoints"])


def test_auth_audit_invalid_ip_fails_before_network():
    with pytest.raises(ValueError):
        auth.audit("example.invalid","100.83.215.83",observed_listeners=set(),
                   node_hostname="joyner")


def test_cutover_same_basename_is_never_enough():
    rows=[
        {"port":1234,"model":"MiniCPM5-Q4.gguf","pid":1},
        {"port":1236,"model":"Ling.gguf","pid":2},
        {"port":18320,"model":"MiniCPM5-Q4.gguf","pid":3},
        {"port":18321,"model":"toolcall.gguf","pid":4},
    ]
    r=cutover.assess(rows,{18320:401,18321:401},
       {"MemAvailable_kib":12*1024*1024,"SwapFree_kib":4*1024*1024},
       correct_host=True,proc_readable=True)
    assert r["verdict"] == "CUTOVER_HOLD"
    assert r["may_stop_inference"] is False
    assert r["may_switch_proxies"] is False
    mini=next(x for x in r["lanes"] if x["source_port"]==1234)
    assert any(x["source_and_candidate_model_basename_equal"] and
               x["missing_credential_rejected"] for x in mini["possible_guarded_targets"])
    assert mini["cutover_ready"] is False
    assert "candidate_model_digest_and_alias_unverified" in mini["reasons"]
    ling=next(x for x in r["lanes"] if x["source_port"]==1236)
    assert all(x["source_and_candidate_model_basename_equal"] is False
               for x in ling["possible_guarded_targets"])
    assert ling["cutover_ready"] is False


def test_cutover_empty_or_ambiguous_process_refuses():
    r=cutover.assess([],{}, {},correct_host=True,proc_readable=False)
    assert r["may_stop_inference"] is False
    assert all("source_process_identity_ambiguous" in x["reasons"] for x in r["lanes"])
    assert r["resource_reasons"]


def test_cutover_arg_parser_refuses_ambiguous_and_unlisted():
    assert cutover.extract_model(["llama-server","--port","55555","--model","m.gguf"]) is None
    assert cutover.extract_model(["llama-server","--port","1234","--port","1236",
                                  "--model","m.gguf"]) is None
    assert cutover.extract_model(["bash","--port","1234","--model","m.gguf"]) is None
    good=cutover.extract_model(["/tmp/llama-server","--port","18320","--model","/models/m.gguf"])
    assert good is not None and good["model"]=="m.gguf"
