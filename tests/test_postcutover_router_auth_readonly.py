"""No-network fail-closed tests for post-cutover Router readback (never GO)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from scripts import verify_postcutover_router_auth_readonly as m

CANDIDATE = "sha256:" + "a" * 64


def synthetic_remote():
    return {
        "direct_tailnet_admin_denied": True, "public_gets_ok": True,
        "admin_get_count": len(m.ADMIN_READS), "public_get_count": 2,
        "body_read": False, "credentials_sent": False, "provider_calls_requested": 0,
    }


def synthetic_client():
    return {
        "authorized_admin_read_ok": True, "admin_read_count": 3,
        "body_read": False, "token_emitted": False, "mutations": 0,
        "provider_requests_sent": 0,
    }


@pytest.mark.parametrize("invalid", [
    "", "git-router:latest", "sha256:short", m.OLD_VULNERABLE,
    "sha256:" + "G" * 64,
])
def test_old_or_invalid_image_never_runs_docker_ssh_or_http(monkeypatch, invalid):
    monkeypatch.setattr(m, "image_readback", lambda:
                        (_ for _ in ()).throw(AssertionError("image guard bypass")))
    report,code = m.validate(invalid,True)
    assert code == 2
    assert report["verdict"] == "UNSAFE_IMAGE_PIN_HOLD"
    assert report["production_cutover_authorized"] is False


def test_new_image_not_running_never_reaches_tailnet(monkeypatch):
    monkeypatch.setattr(m, "image_readback", lambda: (m.OLD_VULNERABLE,True))
    monkeypatch.setattr(m, "strict_tailnet_address", lambda:
                        (_ for _ in ()).throw(AssertionError("remote probe bypass")))
    report,code=m.validate(CANDIDATE,True)
    assert code==2 and report["verdict"]=="IMAGE_OR_APPROVAL_HOLD"
    assert "RUNNING_IMAGE_MISMATCH" in report["blockers"]


def test_explicit_operator_readonly_optin_required_before_network(monkeypatch):
    monkeypatch.setattr(m, "image_readback", lambda: (CANDIDATE,True))
    monkeypatch.setattr(m, "strict_tailnet_address", lambda:
                        (_ for _ in ()).throw(AssertionError("remote probe bypass")))
    report,code=m.validate(CANDIDATE,False)
    assert code==2
    assert "OPERATOR_READONLY_OPT_IN_REQUIRED" in report["blockers"]


def test_stopped_router_denied(monkeypatch):
    monkeypatch.setattr(m, "image_readback", lambda: (CANDIDATE,False))
    monkeypatch.setattr(m, "strict_tailnet_address", lambda:
                        (_ for _ in ()).throw(AssertionError("probe bypass")))
    report,code=m.validate(CANDIDATE,True)
    assert code==2 and "ROUTER_NOT_RUNNING" in report["blockers"]


def test_happy_synthetic_checks_are_still_release_hold(monkeypatch):
    reads=[]
    monkeypatch.setattr(m,"image_readback",lambda: (CANDIDATE,True))
    monkeypatch.setattr(m,"strict_tailnet_address",lambda: "100.64.43.123")
    monkeypatch.setattr(m,"remote_negative_check",lambda ip: reads.append(("remote",ip)))
    monkeypatch.setattr(m,"real_client_check",lambda name: reads.append(("client",name)))
    report,code=m.validate(CANDIDATE,True)
    assert code==0
    assert report["verdict"]=="READONLY_POSTCUTOVER_AUTH_CHECKS_PASS_RELEASE_HOLD"
    assert report["production_cutover_authorized"] is False
    assert report["rollback_verified"] is False
    assert len(reads)==4
    assert reads[0]==("remote","100.64.43.123")
    assert [v for _,v in reads[1:]]==list(m.CLIENTS)


def test_second_image_readback_drift_denies_all_clients(monkeypatch):
    reads=iter([(CANDIDATE,True),(m.OLD_VULNERABLE,True)])
    monkeypatch.setattr(m,"image_readback",lambda:next(reads))
    monkeypatch.setattr(m,"strict_tailnet_address",lambda:"100.64.43.123")
    monkeypatch.setattr(m,"remote_negative_check",lambda ip:True)
    monkeypatch.setattr(m,"real_client_check",lambda name:
                        (_ for _ in ()).throw(AssertionError("client executed")))
    report,code=m.validate(CANDIDATE,True)
    assert code==2 and report["verdict"]=="POSTCUTOVER_ACCEPTANCE_FAILED_HOLD"


def test_tailnet_probe_failure_denies_all_clients(monkeypatch):
    monkeypatch.setattr(m,"image_readback",lambda:(CANDIDATE,True))
    monkeypatch.setattr(m,"strict_tailnet_address",lambda:"100.64.43.123")
    monkeypatch.setattr(m,"remote_negative_check",lambda ip:
                        (_ for _ in ()).throw(m.PostCutoverDenied("SECRET")))
    monkeypatch.setattr(m,"real_client_check",lambda name:
                        (_ for _ in ()).throw(AssertionError("not allowed")))
    report,code=m.validate(CANDIDATE,True)
    assert code==2 and "SECRET" not in json.dumps(report)


@pytest.mark.parametrize("tamper",[
    {"direct_tailnet_admin_denied":False},
    {"public_gets_ok":False},
    {"admin_get_count":3},
    {"public_get_count":3},
    {"body_read":True},
    {"credentials_sent":True},
    {"provider_calls_requested":1},
    {"admin_get_count":True},
])
def test_bad_remote_report_denied(tamper):
    data=synthetic_remote()
    data.update(tamper)
    with pytest.raises(m.PostCutoverDenied):
        m.checked_result(json.dumps(data),0,synthetic_remote())


@pytest.mark.parametrize("tamper",[
    {"authorized_admin_read_ok":False},
    {"admin_read_count":2},
    {"token_emitted":True},
    {"mutations":1},
    {"provider_requests_sent":1},
    {"admin_read_count":True},
])
def test_bad_client_report_denied(tamper):
    data=synthetic_client()
    data.update(tamper)
    with pytest.raises(m.PostCutoverDenied):
        m.checked_result(json.dumps(data),0,synthetic_client())


@pytest.mark.parametrize("raw,rc", [
    ("",0),("not-json",0),("[]",0),(json.dumps(synthetic_remote()),2),
])
def test_unparseable_or_failed_report_denied(raw,rc):
    with pytest.raises(m.PostCutoverDenied):
        m.checked_result(raw,rc,synthetic_remote())


def test_exact_passive_tailnet_route_inventory_is_fixed_and_safe():
    import ast

    code = ast.parse(m.REMOTE_CHECK)
    route_assignment = next(
        node for node in code.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "paths"
                for target in node.targets)
    )
    assert tuple(ast.literal_eval(route_assignment.value)) == m.ADMIN_READS
    assert len(m.ADMIN_READS) == 12
    assert len(set(m.ADMIN_READS)) == 12
    assert all(route.startswith("/admin/") for route in m.ADMIN_READS)
    assert "/admin/providers/health" not in m.ADMIN_READS
    assert "/admin/ops/preflight" not in m.ADMIN_READS
    assert "/admin/context/graph" not in m.ADMIN_READS
    assert "POST" not in m.REMOTE_CHECK
    assert "read(" not in m.REMOTE_CHECK


def test_remote_probe_uses_known_host_and_fixed_source(monkeypatch):
    seen={}
    def fake(argv,**kwargs):
        seen["argv"]=argv
        seen["source"]=kwargs.get("input_text")
        return 0,json.dumps(synthetic_remote())
    monkeypatch.setattr(m,"command",fake)
    m.remote_negative_check("100.64.43.123")
    assert seen["argv"][0]=="ssh"
    assert "StrictHostKeyChecking=yes" in seen["argv"]
    assert seen["argv"][-3:] == ["python3","-","100.64.43.123"]
    assert seen["argv"].count("xwing")==1
    assert "method=\"GET\"" in seen["source"]


def test_client_check_uses_allowlisted_container_only(monkeypatch):
    seen=[]
    def fake(argv,**kwargs):
        seen.append((argv,kwargs.get("input_text")))
        return 0,json.dumps(synthetic_client())
    monkeypatch.setattr(m,"command",fake)
    for name in m.CLIENTS:
        m.real_client_check(name)
    assert all(row[0][0:3]==["docker","exec","-i"] for row in seen)
    assert all(row[0][-2:]==["python3","-"] for row in seen)
    assert all('method="GET"' in row[1] for row in seen)
    with pytest.raises(m.PostCutoverDenied, match="unapproved client"):
        m.real_client_check("git-router-1")


@pytest.mark.parametrize("ip",[
    "192.168.1.7","8.8.8.8","100.10.0.1","not-an-ip","100.64.43.123\n100.64.43.124"
])
def test_tailnet_identity_rejects_other_targets(monkeypatch,ip):
    monkeypatch.setattr(m,"command",lambda argv,**kwargs:(0,ip))
    with pytest.raises(m.PostCutoverDenied):
        m.strict_tailnet_address()


def test_tailnet_identity_accepts_single_cgnat_ip(monkeypatch):
    monkeypatch.setattr(m,"command",lambda argv,**kwargs:(0,"100.64.43.123\n"))
    assert m.strict_tailnet_address()=="100.64.43.123"


def test_bounded_command_does_not_print_stderr_or_secrets(monkeypatch):
    fake_secret="SUPERPRIVATE_123"
    def fake(argv,**kwargs):
        return SimpleNamespace(returncode=1,stdout="",stderr=fake_secret)
    monkeypatch.setattr(m.subprocess,"run",fake)
    code,raw=m.command(["docker","inspect",m.ROUTER])
    assert code==1 and fake_secret not in raw
    with pytest.raises(m.PostCutoverDenied) as exc:
        m.image_readback()
    assert fake_secret not in str(exc.value)


def test_real_host_guard_only_reads_existing_router(monkeypatch):
    image={"Name":"/git-router-1","Image":CANDIDATE,
           "State":{"Status":"running"}}
    calls=[]
    def fake(argv,**kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0,stdout=json.dumps([image]),stderr="")
    monkeypatch.setattr(m.subprocess,"run",fake)
    assert m.image_readback()==(CANDIDATE,True)
    assert calls==[["docker","inspect","git-router-1"]]


def test_cli_no_optin_never_probes_remote(monkeypatch,capsys):
    monkeypatch.setattr(m,"image_readback",lambda:(CANDIDATE,True))
    monkeypatch.setattr(m,"strict_tailnet_address",lambda:
                        (_ for _ in ()).throw(AssertionError("optin ignored")))
    monkeypatch.setattr(m.argparse.ArgumentParser,"parse_args",lambda self:
                        SimpleNamespace(expected_candidate_image=CANDIDATE,
                                        authorize_readonly_check=False))
    assert m.main()==2
    raw=capsys.readouterr().out
    assert json.loads(raw)["production_cutover_authorized"] is False


def test_remote_and_client_embedded_scripts_compile_without_running():
    compile(m.REMOTE_CHECK,"remote-test","exec")
    compile(m.CLIENT_CHECK,"client-test","exec")
    assert "/v1/chat/completions" not in m.REMOTE_CHECK
    assert "/admin/load" not in m.REMOTE_CHECK
    assert "POST" not in m.CLIENT_CHECK
