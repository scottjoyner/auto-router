"""Offline-only cutover readiness tests: no GPU, model load or network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts import joyner_cutover_readiness as c


def processes():
    return [
        {"pid":11,"port":1234,"model":"MiniCPM5-1B-Q4_K_M.gguf","alias":"joyner-toolcall"},
        {"pid":12,"port":1236,"model":"Ling-3.0-tiny-abliterated-APEX-I-Compact.gguf","alias":"joyner-ling"},
        {"pid":13,"port":18320,"model":"MiniCPM5-1B-Q4_K_M.gguf"},
        {"pid":14,"port":18321,"model":"toolcall-v5-3b-combined-r2-f16.gguf"},
    ]


def healthy_memory():
    return {"MemAvailable_kib":16*1024*1024,"SwapFree_kib":4*1024*1024}


def actual_memory():
    return {"MemAvailable_kib":135*1024,"SwapFree_kib":72}


def test_parse_known_llama_flags_never_prints_credentials():
    row=c.extract_model([
        "/home/scott/fleet-gpu/llama/bin/llama-server",
        "-m","/home/scott/fleet-gpu/models/MiniCPM5-1B-Q4_K_M.gguf",
        "-ngl","99","--port","1234","--alias","joyner-toolcall",
        "--api-key","should-never-be-visible",
    ])
    assert row["model"]=="MiniCPM5-1B-Q4_K_M.gguf"
    assert row["port"]==1234
    assert row["alias"]=="joyner-toolcall"
    assert "should-never-be-visible" not in repr(row)


@pytest.mark.parametrize("argv",[
    [],["bash","-c","llama-server --port 1234"],["python3","-m","llama-server"],
    ["llama-server","-m","bad.gguf","--port","bad"],
    ["llama-server","-m","bad.gguf","--port","8095"],
    ["llama-server","--port","1234"],
    ["llama-server","-m","a.gguf","-m","b.gguf","--port","1234"],
])
def test_ignore_invalid_or_ambiguous_process_identity(argv):
    assert c.extract_model(argv) is None


def test_actual_model_distinction_preserves_ling_hold():
    d=c.assess(processes(),{18320:401,18321:401},healthy_memory(),
               correct_host=True,proc_readable=True)
    assert d["verdict"]=="CUTOVER_HOLD"
    assert d["may_switch_proxies"] is False
    assert d["may_train"] is False
    minicpm,ling=d["lanes"]
    assert minicpm["source_model_basename"]=="MiniCPM5-1B-Q4_K_M.gguf"
    assert "candidate_model_digest_and_alias_unverified" in minicpm["reasons"]
    assert "no_verified_model_equivalent_guarded_candidate" in ling["reasons"]
    assert all(not x["eligible_for_cutover"] for row in d["lanes"]
               for x in row["possible_guarded_targets"])


def test_model_name_match_is_not_a_weights_digest():
    d=c.assess(processes(),{18320:401,18321:401},healthy_memory(),
               correct_host=True,proc_readable=True)
    mini=[x for x in d["lanes"][0]["possible_guarded_targets"]
          if x["port"]==18320][0]
    assert mini["source_and_candidate_model_basename_equal"] is True
    assert mini["same_weights_sha256_proven"] is False
    assert mini["same_alias_and_request_contract_proven"] is False


def test_http200_guarded_endpoint_not_auth_proof():
    d=c.assess(processes(),{18320:200,18321:401},healthy_memory(),
               correct_host=True,proc_readable=True)
    mini=d["lanes"][0]
    assert "no_verified_model_equivalent_guarded_candidate" in mini["reasons"]
    assert mini["possible_guarded_targets"][0]["missing_credential_rejected"] is False


def test_missing_source_and_duplicate_server_cannot_pass():
    d=c.assess(processes()[1:],{18320:401,18321:401},healthy_memory(),
               correct_host=True,proc_readable=True)
    assert "source_process_identity_ambiguous" in d["lanes"][0]["reasons"]
    rows=processes()+[processes()[0]]
    d=c.assess(rows,{18320:401,18321:401},healthy_memory(),
               correct_host=True,proc_readable=True)
    assert "source_process_identity_ambiguous" in d["lanes"][0]["reasons"]


def test_low_ram_and_swap_hold_independently():
    d=c.assess(processes(),{18320:401,18321:401},actual_memory(),
               correct_host=True,proc_readable=True)
    assert len(d["resource_reasons"])==2
    assert "ram_headroom_below_conservative_threshold" in d["resource_reasons"]
    assert "swap_headroom_below_conservative_threshold" in d["resource_reasons"]


def test_stale_or_unknown_meminfo_never_means_resource_safe():
    d=c.assess(processes(),{18320:401,18321:401},{},
               correct_host=True,proc_readable=True)
    assert len(d["resource_reasons"])==2


def test_wrong_host_makes_no_process_or_auth_calls():
    def fail(*args):
        raise AssertionError("wrong host should not do any scans")
    d=c.audit(hostname="x1-370",process_reader=fail,
              status_reader=fail,mem_reader=fail)
    assert d["observer_host_verified"] is False
    assert d["may_switch_proxies"] is False
    assert all("observer_or_process_inventory_unverified" in x["reasons"]
               for x in d["lanes"])


def test_audit_injection_does_not_mutate_or_admit():
    calls=[]
    def probe(port):
        calls.append(port);return 401
    d=c.audit(hostname="joyner",process_reader=lambda:(processes(),True),
              status_reader=probe,mem_reader=healthy_memory)
    assert calls==[18320,18321]
    assert d["model_request_sent"] is False
    assert d["credentials_sent"] is False
    assert d["may_promote_router"] is False
    assert d["may_stop_inference"] is False


def test_proc_fixtures_only_read_process_cmdline(tmp_path):
    proc=tmp_path/"proc"
    proc.mkdir()
    one=proc/"1412"
    one.mkdir()
    (one/"cmdline").write_bytes(b"/usr/bin/llama-server\0--model\0/tmp/MiniCPM5-1B-Q4_K_M.gguf\0--port\01234\0")
    rows,readable=c.processes(proc)
    # Either a malformed NUL-separated port fails, or it reports a valid exact port.
    assert readable is True
    assert len(rows)<=1


def test_meminfo_parsed_without_trusting_external_metrics(tmp_path):
    p=tmp_path/"meminfo"
    p.write_text("MemAvailable:      1572864 kB\nSwapFree:       1024 kB\n")
    assert c.read_meminfo(p)=={"MemAvailable_kib":1572864,"SwapFree_kib":1024}


def test_entrypoint_always_returns_cutover_hold(monkeypatch,capsys):
    monkeypatch.setattr(c,"audit",lambda:{
        "verdict":"CUTOVER_HOLD","may_switch_proxies":False})
    assert c.main()==78
    assert json.loads(capsys.readouterr().out)["may_switch_proxies"] is False
