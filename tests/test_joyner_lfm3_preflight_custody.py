"""Synthetic, key-free Joyner LFM3 preflight custody tests."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from scripts import joyner_lfm3_preflight_custody as p


def fixture(tmp_path, monkeypatch, *, bridge=True, source=None):
    model = tmp_path / p.SOURCE_NAME
    contents = (source if source is not None else
                b"from pathlib import Path\n# synthetic offline preflight\n")
    model.write_bytes(contents)
    monkeypatch.setattr(p, "SOURCE_SHA256",
                        hashlib.sha256(contents).hexdigest())
    calls = []

    def runner(args):
        calls.append(tuple(args))
        assert args[:2] == ["systemctl", "--user"]
        assert args[2] in ("cat", "show")
        assert args[3] in (p.ALLOWED_UNITS[0] + ".service",
                           p.ALLOWED_UNITS[1] + ".service")
        is_lfm3 = args[3].startswith("lfm3-")
        if args[2] == "cat":
            if is_lfm3:
                return 0, f"ExecStartPre=/usr/bin/python3 {model}\n" \
                          "Environment=SYNTHETIC_SECRET_VALUE\n" \
                          "ExecStart=/usr/bin/llama-server --api-key-file /SYNTHETIC-SECRET\n"
            return 0, (
                "ExecStartPre=/usr/bin/curl -fsS --max-time 2 "
                "http://127.0.0.1:19175/health\n"
                if bridge else
                "ExecStartPre=/usr/bin/echo not-a-health-guard\n"
            )
        return 0, ("ExecStartPre={ path=/usr/bin/python3 ; "
                   "code=exited ; status=" + ("1" if is_lfm3 else "7")
                   + " }\n")
    lfm3_command = f"/usr/bin/python3 {model}"
    bridge_command = (
        "/usr/bin/curl -fsS --max-time 2 "
        "http://127.0.0.1:19175/health"
        if bridge else "/usr/bin/echo not-a-health-guard"
    )
    monkeypatch.setattr(
        p, "LFM3_COMMAND_SHA256",
        hashlib.sha256(lfm3_command.encode()).hexdigest()
    )
    monkeypatch.setattr(
        p, "BRIDGE_COMMAND_SHA256",
        hashlib.sha256(bridge_command.encode()).hexdigest()
    )
    return model, runner, calls


def test_exact_preflight_custody_and_bridge_dependency(tmp_path,monkeypatch):
    model,runner,calls=fixture(tmp_path,monkeypatch)
    receipt=p.audit(hostname="joyner",runner=runner,home=tmp_path,
                    uid=os.geteuid(),gpu_reader=lambda:False,
                    ram_reader=lambda:490)
    assert receipt["pinned_preflight_contract"] == {
        "known_preflight_source_sha256_verified": True,
        "preflight_source_owned_by_observer": True,
        "gpu_vram_30gib_guard_pinned": True,
        "system_ram_2200mib_guard_pinned": True,
        "model_integrity_guard_pinned": True,
        "keyfile_metadata_guard_pinned": True,
    }
    assert receipt["lfm3_preflight_exit_nonzero_observed"] is True
    assert receipt["bridge_preflight_exit_nonzero_observed"] is True
    assert receipt["bridge_local_lfm3_health_dependency_verified"] is True
    assert receipt["lfm3_preflight_launch_command_pinned"] is True
    assert receipt["bridge_preflight_launch_command_pinned"] is True
    assert receipt["r9700_pci_present"] is False
    assert receipt["memavailable_mib"] == 490
    assert receipt["vram_guard_would_refuse_if_reached"] is True
    assert receipt["ram_guard_would_refuse_if_reached"] is True
    assert receipt["verdict"] == "HOLD"
    assert receipt["exit_code"] == 78
    assert receipt["key_file_opened"] is False
    assert receipt["model_file_opened"] is False
    assert receipt["preflight_executed"] is False
    assert receipt["service_change_authorized"] is False
    assert all(t[0:2] == ("systemctl","--user") for t in calls)
    assert len(calls) == 4
    raw = json.dumps(receipt)
    assert "SYNTHETIC_SECRET" not in raw
    assert "--api-key-file" not in raw
    assert str(model) not in raw


def test_wrong_host_makes_no_system_calls(tmp_path):
    def prohibited(*a,**kw):
        raise AssertionError("unexpected read on wrong host")
    receipt=p.audit(hostname="x1-370",runner=prohibited,
                    gpu_reader=prohibited,ram_reader=prohibited)
    assert receipt["observer_host_verified"] is False
    assert receipt["verdict"] == "HOLD"
    assert receipt["pinned_preflight_contract"] == {}


@pytest.mark.parametrize("status,expected", [
    ("code=exited ; status=1",True),
    ("code=exited ; status=7",True),
    ("code=exited; status=0",False),
    ("code=killed ; status=1",None),
    ("code=exited ; status=SYNTHETIC_SECRET",None),
    ("",None),
])
def test_bounded_systemd_preflight_exit_status(status,expected):
    raw=f"ExecStartPre={{ path=/private/secret ; {status} }}\n"
    assert p._preflight_is_failing(raw) is expected


def test_ambiguous_preflight_status_is_unknown():
    value=("ExecStartPre={code=exited ; status=1} "
           "{code=exited ; status=7}")
    assert p._preflight_is_failing(value) is None


def test_unknown_or_unpinned_source_never_asserts_guards(tmp_path,monkeypatch):
    model,runner,_=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(p,"SOURCE_SHA256","f"*64)
    receipt=p.audit(hostname="joyner",runner=runner,home=tmp_path,
                    uid=os.geteuid(),gpu_reader=lambda:False,ram_reader=lambda:10)
    assert not receipt["pinned_preflight_contract"][
        "known_preflight_source_sha256_verified"]
    assert not receipt["vram_guard_would_refuse_if_reached"]
    assert not receipt["ram_guard_would_refuse_if_reached"]
    assert receipt["verdict"]=="HOLD"


def test_symlinked_source_denied(tmp_path,monkeypatch):
    model,runner,_=fixture(tmp_path,monkeypatch)
    new=tmp_path/"replacement.py"
    model.rename(new)
    model.symlink_to(new)
    receipt=p.audit(hostname="joyner",runner=runner,home=tmp_path,
                    uid=os.geteuid(),gpu_reader=lambda:False,ram_reader=lambda:10)
    assert not receipt["pinned_preflight_contract"][
        "known_preflight_source_sha256_verified"]


def test_source_outside_home_never_accepted(tmp_path,monkeypatch):
    _,runner,_=fixture(tmp_path,monkeypatch)
    report=p._script_contract(Path("/tmp/unsafe/server_preflight.py"),
                             tmp_path,os.geteuid())
    assert not report["known_preflight_source_sha256_verified"]


def test_changed_source_after_pin_is_hold(tmp_path,monkeypatch):
    model,runner,_=fixture(tmp_path,monkeypatch)
    model.write_bytes(b"changed source")
    receipt=p.audit(hostname="joyner",runner=runner,home=tmp_path,
                    uid=os.geteuid(),gpu_reader=lambda:False,ram_reader=lambda:10)
    assert not receipt["pinned_preflight_contract"][
        "known_preflight_source_sha256_verified"]
    assert receipt["verdict"]=="HOLD"


@pytest.mark.parametrize("command,expected",[
    ("/usr/bin/curl -fsS --max-time 2 http://127.0.0.1:19175/health",True),
    ("/usr/bin/curl --fail --max-time 2 http://127.0.0.1:19175/health",True),
    ("/usr/bin/curl http://127.0.0.1:19175/health",False),
    ("/usr/bin/curl -fsS http://127.0.0.1:19176/health",False),
    ("/usr/bin/echo http://127.0.0.1:19175/health",False),
])
def test_bridge_dependency_requires_exact_health_and_fail_mode(command,expected):
    assert p._bridge_health_guard(command) is expected


def test_bridge_contract_not_verified_if_wrong_route(tmp_path,monkeypatch):
    model,runner,_=fixture(tmp_path,monkeypatch,bridge=False)
    receipt=p.audit(hostname="joyner",runner=runner,home=tmp_path,
                    uid=os.geteuid(),gpu_reader=lambda:False,ram_reader=lambda:10)
    assert receipt["bridge_local_lfm3_health_dependency_verified"] is False


def test_gpu_census_partial_is_unknown(tmp_path):
    root=tmp_path/"pci"
    root.mkdir()
    (root/"invalid").mkdir()
    assert p._gpu_present(root) is None
    assert p._gpu_present(root/"missing") is None


def test_gpu_census_valid_missing_and_present(tmp_path):
    root=tmp_path/"pci"
    root.mkdir()
    d=root/"0000:0d:00.0";d.mkdir()
    (d/"vendor").write_text("0x1002")
    (d/"device").write_text("0x164e")
    assert p._gpu_present(root) is False
    second=root/"0000:05:00.0";second.mkdir()
    (second/"vendor").write_text("0x1002")
    (second/"device").write_text("0x7551")
    assert p._gpu_present(root) is True


def test_ram_missing_or_malformed_does_not_claim_low(tmp_path):
    m=tmp_path/"meminfo"
    assert p._ram_available(m) is None
    m.write_text("MemAvailable: SYNTHETIC_SECRET kB\n")
    assert p._ram_available(m) is None
    m.write_text("MemAvailable: 2457600 kB\n")
    assert p._ram_available(m) == 2400


def test_unknown_gpu_or_memory_never_claims_deterministic_guard(tmp_path,monkeypatch):
    model,runner,_=fixture(tmp_path,monkeypatch)
    receipt=p.audit(hostname="joyner",runner=runner,home=tmp_path,
                    uid=os.geteuid(),gpu_reader=lambda:None,ram_reader=lambda:None)
    assert receipt["vram_guard_would_refuse_if_reached"] is False
    assert receipt["ram_guard_would_refuse_if_reached"] is False
    assert receipt["service_change_authorized"] is False


def test_script_custody_no_keyfile_read_even_if_private_key_argument_present(tmp_path,monkeypatch):
    model,runner,_=fixture(tmp_path,monkeypatch)
    _=p.audit(hostname="joyner",runner=runner,home=tmp_path,
              uid=os.geteuid(),gpu_reader=lambda:False,ram_reader=lambda:100)
    assert not (tmp_path/"SYNTHETIC-SECRET").exists()


def test_main_exit_code_78_and_json_redaction(monkeypatch,capsys):
    monkeypatch.setattr(p,"audit",lambda:{"verdict":"HOLD","exit_code":78,
                                          "service_change_authorized":False})
    assert p.main()==78
    d=json.loads(capsys.readouterr().out)
    assert d["verdict"]=="HOLD"
    assert not d["service_change_authorized"]


def test_same_looking_but_changed_lfm3_launch_is_not_trusted(tmp_path,monkeypatch):
    model,runner,_=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(p,"LFM3_COMMAND_SHA256","0"*64)
    report=p.audit(hostname="joyner",runner=runner,home=tmp_path,
                   uid=os.geteuid(),gpu_reader=lambda:False,ram_reader=lambda:10)
    assert report["lfm3_preflight_launch_command_pinned"] is False
    assert report["pinned_preflight_contract"]=={}
    assert report["vram_guard_would_refuse_if_reached"] is False


def test_same_looking_but_changed_bridge_command_not_trusted(tmp_path,monkeypatch):
    model,runner,_=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(p,"BRIDGE_COMMAND_SHA256","0"*64)
    report=p.audit(hostname="joyner",runner=runner,home=tmp_path,
                   uid=os.geteuid(),gpu_reader=lambda:False,ram_reader=lambda:10)
    assert report["bridge_preflight_launch_command_pinned"] is False
    assert report["bridge_local_lfm3_health_dependency_verified"] is False


def test_unavailable_systemd_config_holds_without_assuming_guard(tmp_path,monkeypatch):
    model,runner,_=fixture(tmp_path,monkeypatch)
    def partial(args):
        if args[2]=="cat":
            return 1,"SYNTHETIC_SECRET"
        return runner(args)
    report=p.audit(hostname="joyner",runner=partial,home=tmp_path,
                   uid=os.geteuid(),gpu_reader=lambda:False,ram_reader=lambda:10)
    assert report["pinned_preflight_contract"]=={}
    assert report["bridge_local_lfm3_health_dependency_verified"] is False
    assert "SYNTHETIC_SECRET" not in json.dumps(report)


def test_unknown_preflight_exit_does_not_forge_fail(tmp_path,monkeypatch):
    model,runner,_=fixture(tmp_path,monkeypatch)
    def missing_show(args):
        if args[2]=="show":
            return 1,""
        return runner(args)
    report=p.audit(hostname="joyner",runner=missing_show,home=tmp_path,
                   uid=os.geteuid(),gpu_reader=lambda:False,ram_reader=lambda:10)
    assert report["lfm3_preflight_exit_nonzero_observed"] is None
    assert report["bridge_preflight_exit_nonzero_observed"] is None
    assert report["verdict"]=="HOLD"
