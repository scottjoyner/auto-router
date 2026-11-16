"""Synthetic read-only Joyner LFM3 outage observer acceptance.

No live SSH, systemd, sockets, GPU or model loads in unit tests.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from scripts import joyner_lfm3_outage_witness as w


def fixture_runner(*, lfm3="activating", bridge="activating",
                   legacy_restarts=1700, lfm3_restarts=300,
                   bridge_restarts=600, with_listener=False):
    calls = []
    status = {
        "lfm3-joyner-inference": ("exit-code", lfm3, "auto-restart", lfm3_restarts, 0),
        "kipnerter-lfm3-r9700-bridge": ("exit-code", bridge, "auto-restart", bridge_restarts, 0),
        "joyner-lane-1234": ("exit-code", "activating", "auto-restart", legacy_restarts, 0),
        "fleet-joyner-gpu": ("success", "active", "running", 0, 1839),
        "fleet-joyner-ling": ("success", "active", "running", 0, 1842),
    }

    def run(args):
        calls.append(args)
        if args[0] == "ss":
            raw = (
                "LISTEN 0 512 0.0.0.0:1234 0.0.0.0:*\n"
                "LISTEN 0 512 0.0.0.0:1236 0.0.0.0:*\n"
            )
            if with_listener:
                raw += "LISTEN 0 128 127.0.0.1:19175 0.0.0.0:*\n"
            return 0, raw
        assert args[:3] == ["systemctl", "--user", "show"]
        name = args[3].removesuffix(".service")
        result, active, sub, restarts, pid = status[name]
        return 0, (
            "LoadState=loaded\n"
            f"ActiveState={active}\nSubState={sub}\nResult={result}\n"
            f"NRestarts={restarts}\nMainPID={pid}\nExecMainStatus=1\n"
            "StateChangeTimestamp=Sun 2026-11-15 17:30:00 EST\n"
            "ExecStart=/private/APIKEY=synthetic-secret\n"
            "Environment=SECRET_SHOULD_NOT_LEAK\n"
        )
    return run, calls, status


def boot_record(identifier="a"*8+"-"+"b"*4+"-"+"c"*4+"-"+"d"*4+"-"+"e"*12,
                uptime=5000):
    return {"boot_id_sha256": hashlib.sha256(identifier.encode()).hexdigest(),
            "uptime_seconds": uptime, "verified": True}


def test_exact_failing_host_witness_holds_and_redacts():
    runner,calls,_ = fixture_runner()
    seen = []
    def sleep(seconds):
        seen.append(seconds)
    receipt = w.audit(hostname="joyner",runner=runner,health=lambda:None,
                      boot=boot_record,memory=lambda:{"MemAvailable_mib":1500},
                      sleep=sleep)
    assert receipt["verdict"] == "OUTAGE_HOLD"
    assert receipt["exit_code"] == 78
    assert seen == [2]
    assert receipt["same_boot_during_observation"] is True
    assert receipt["local_lfm3_health_http"] is None
    assert receipt["final_ports"]["listening"] == {
        "1234": True, "1236": True, "19175": False
    }
    assert receipt["resources"]["MemAvailable_mib"] == 1500
    assert len(receipt["restarts"]) == 5
    assert all(i["restart_count_delta"] == 0 for i in receipt["restarts"])
    raw = json.dumps(receipt)
    assert "synthetic-secret" not in raw
    assert "ExecStart=" not in raw
    assert "Environment=" not in raw
    assert receipt["router_cutover_authorized"] is False
    assert receipt["unit_start_stop_restart_performed"] is False
    assert all(row[0] in ("systemctl","ss") for row in calls)
    assert len(calls) == 12


def test_wrong_hostname_invokes_no_observers():
    def forbidden(*args,**kwargs):
        raise AssertionError("unexpected live source")
    result = w.audit(hostname="x1-370",runner=forbidden,
                     health=forbidden,memory=forbidden,boot=forbidden,sleep=forbidden)
    assert result["verdict"] == "HOLD"
    assert result["observer_host_verified"] is False
    assert result["final_units"] == []
    assert result["local_lfm3_health_http"] is None


def test_allowlisted_units_and_safe_unit_property_projection():
    runner,calls,_=fixture_runner()
    unit=w.unit_snapshot("lfm3-joyner-inference",runner)
    assert unit["active"]=="activating"
    assert unit["restart_count"]==300
    assert unit["main_pid_present"] is False
    assert "ExecStart" not in unit
    assert len(calls)==1
    with pytest.raises(ValueError,match="not_allowlisted"):
        w.unit_snapshot("arbitrary.service",runner)


@pytest.mark.parametrize("value", ["SYNTHETIC_SECRET", "Unit=somefile", "start-time", "root=True"])
def test_unknown_enum_not_emitted(value):
    parsed=w.parse_properties(
        f"LoadState={value}\nActiveState={value}\nResult={value}\n"
        "ExecStart=PRIVATE_TOKEN=abc\nEnvironment=SECRET_KEY\n"
    )
    assert all(parsed[x]=="unverified" for x in ("LoadState","ActiveState","Result"))
    assert value not in str(parsed)
    assert "SECRET" not in str(parsed)


def test_bad_numeric_property_never_invented():
    d=w.parse_properties("NRestarts=not-a-number\nMainPID=-3\nExecMainStatus=secret\n")
    assert d["NRestarts"] is None
    assert d["MainPID"] is None
    assert d["ExecMainStatus"] is None


def test_heartbeat_detects_growth_without_approving():
    run1,_,_=fixture_runner(lfm3_restarts=300)
    run2,_,_=fixture_runner(lfm3_restarts=310)
    before=w.capture(runner=run1,health=lambda:None,boot=boot_record,
                     memory=lambda:{})
    after=w.capture(runner=run2,health=lambda:None,boot=boot_record,
                    memory=lambda:{})
    report=w.evaluate(before,after,host_ok=True)
    changes={x["unit"]:x for x in report["restarts"]}
    assert changes["lfm3-joyner-inference"]["restart_count_delta"]==10
    assert report["verdict"]=="OUTAGE_HOLD"
    assert report["service_change_authorized"] is False


def test_counter_decrement_is_not_recovery_evidence():
    run1,_,_=fixture_runner(lfm3_restarts=300)
    run2,_,_=fixture_runner(lfm3_restarts=1)
    before=w.capture(runner=run1,health=lambda:None,boot=boot_record,
                     memory=lambda:{})
    after=w.capture(runner=run2,health=lambda:None,boot=boot_record,
                    memory=lambda:{})
    report=w.evaluate(before,after,host_ok=True)
    item=next(x for x in report["restarts"]
              if x["unit"]=="lfm3-joyner-inference")
    assert item["restart_count_delta"] is None
    assert report["verdict"]=="OUTAGE_HOLD"


def test_cross_boot_keeps_hold():
    run,_,_=fixture_runner()
    before=w.capture(runner=run,health=lambda:None,boot=lambda:boot_record("a"*36),
                     memory=lambda:{})
    after=w.capture(runner=run,health=lambda:None,boot=lambda:boot_record("b"*36),
                    memory=lambda:{})
    assert not w.evaluate(before,after,host_ok=True)["same_boot_during_observation"]


def test_healthy_status_still_never_grants_deployment():
    run,_,_=fixture_runner(lfm3="active",with_listener=True)
    before=w.capture(runner=run,health=lambda:200,boot=boot_record,
                     memory=lambda:{})
    report=w.evaluate(before,before,host_ok=True)
    assert report["verdict"]=="HOLD"
    assert report["router_cutover_authorized"] is False
    assert report["lease_change_authorized"] is False


def test_socket_census_does_not_accept_established_connections():
    r=w.ports_snapshot(lambda args:(0,
         "ESTAB 0 0 127.0.0.1:19175 127.0.0.1:1234\n"
         "LISTEN 0 512 0.0.0.0:1234 0.0.0.0:*\n"))
    assert r["listening"]["19175"] is False
    assert r["listening"]["1234"] is True


def test_missing_socket_inventory_is_unknown_not_no_listener():
    d=w.ports_snapshot(lambda args:(1,"PRIVATE_ERROR"))
    assert d["listening"]["19175"] is None
    assert d["socket_inventory_usable"] is False
    assert "PRIVATE" not in str(d)


def test_meminfo_missing_and_valid(tmp_path):
    p=tmp_path/"meminfo"
    assert w._read_memory(p)=={}
    p.write_text("MemAvailable: 1024000 kB\nSwapTotal: 4096000 kB\nSwapFree: 1000 kB\nSecret:123\n")
    assert w._read_memory(p)=={
        "MemAvailable_mib":1000,"SwapTotal_mib":4000,"SwapFree_mib":0
    }


def test_boot_id_hash_not_raw(tmp_path):
    p=tmp_path/"boot_id";t=tmp_path/"uptime"
    identity="16843b81-62ad-4475-ad0b-3996cdef1325"
    p.write_text(identity+"\n");t.write_text("5662.0 120.0\n")
    d=w._boot(p,t)
    assert d["verified"] and d["uptime_seconds"]==5662
    assert identity not in str(d)
    p.write_text("invalid-secret-in-boot")
    assert w._boot(p,t)["verified"] is False


def test_main_json_and_hold(monkeypatch,capsys):
    monkeypatch.setattr(w,"audit",lambda:{"verdict":"OUTAGE_HOLD","exit_code":78})
    assert w.main()==78
    assert json.loads(capsys.readouterr().out)["exit_code"]==78


def pci_fixture(tmp_path, *, include_discrete=True, driver=True,
                vram_gib=32, extra_discrete=False):
    root = tmp_path / "sys" / "bus" / "pci" / "devices"
    root.mkdir(parents=True)
    igpu = root / "0000:0d:00.0"
    igpu.mkdir()
    (igpu / "vendor").write_text("0x1002\n")
    (igpu / "device").write_text("0x164e\n")
    if include_discrete:
        d = root / "0000:03:00.0"
        d.mkdir()
        (d / "vendor").write_text("0x1002\n")
        (d / "device").write_text("0x7551\n")
        (d / "mem_info_vram_total").write_text(str(vram_gib*1024**3))
        (d / "mem_info_gtt_total").write_text(str(8*1024**3))
        (d / "mem_info_gtt_used").write_text(str(3*1024**3))
        if driver:
            drv = tmp_path / "drivers" / "amdgpu"
            drv.mkdir(parents=True)
            (d / "driver").symlink_to(drv)
    if extra_discrete:
        d = root / "0000:05:00.0"
        d.mkdir()
        (d / "vendor").write_text("0x1002\n")
        (d / "device").write_text("0x7551\n")
    return root


def test_hardware_detects_r9700_pci_and_amdgpu(tmp_path):
    root=pci_fixture(tmp_path)
    report=w.read_r9700_hardware(root)
    assert report["pci_inventory_usable"] is True
    assert report["r9700_pci_present"] is True
    assert report["r9700_unique_identity"] is True
    assert report["r9700_amdgpu_bound"] is True
    assert report["r9700_vram_total_mib"]==32768
    assert report["r9700_gtt_free_mib"]==5120
    assert report["r9700_pci_location"]=="0000:03:00.0"


def test_hardware_regression_only_raphael_means_no_discrete(tmp_path):
    root=pci_fixture(tmp_path,include_discrete=False)
    report=w.read_r9700_hardware(root)
    assert report["pci_inventory_usable"] is True
    assert report["r9700_pci_present"] is False
    assert report["r9700_amdgpu_bound"] is None
    assert report["r9700_vram_total_mib"] is None
    assert report["r9700_pci_location"] is None


def test_pci_enumeration_unavailable_is_unknown_not_absence(tmp_path):
    report=w.read_r9700_hardware(tmp_path/"not-present")
    assert report["pci_inventory_usable"] is False
    assert report["r9700_pci_present"] is None


def test_amd_device_without_driver_is_not_gpu_ready(tmp_path):
    report=w.read_r9700_hardware(pci_fixture(tmp_path,driver=False))
    assert report["r9700_pci_present"] is True
    assert report["r9700_amdgpu_bound"] is False


def test_duplicate_hardware_identity_refuses_unique_gpu_custody(tmp_path):
    root=pci_fixture(tmp_path,extra_discrete=True)
    report=w.read_r9700_hardware(root)
    assert report["r9700_pci_present"] is True
    assert report["r9700_unique_identity"] is False
    assert report["r9700_amdgpu_bound"] is None
    assert report["r9700_pci_location"] is None


def test_discrete_gpu_reappears_without_becoming_release_authorization(tmp_path):
    report=w.read_r9700_hardware(pci_fixture(tmp_path))
    runner,_,_ = fixture_runner(lfm3="activating")
    base=lambda: w.capture(runner=runner,health=lambda:None,
                           boot=boot_record,memory=lambda:{},hardware=lambda:report)
    verdict=w.evaluate(base(),base(),host_ok=True)
    assert verdict["verdict"]=="OUTAGE_HOLD"
    assert verdict["same_pci_identity_during_observation"] is True
    assert verdict["r9700_hardware"]["r9700_amdgpu_bound"] is True
    assert verdict["r9700_runtime_capability_verified"] is False
    assert verdict["lfm3_failure_attributed_to_r9700_loss"] is False
    assert verdict["service_change_authorized"] is False


def test_hardware_readout_does_not_include_raw_device_config(tmp_path):
    root=pci_fixture(tmp_path)
    (root/"0000:03:00.0"/"config").write_text("SYNTHETIC-SECRET")
    report=w.read_r9700_hardware(root)
    assert "SYNTHETIC-SECRET" not in json.dumps(report)


def test_partial_pci_entry_read_failure_is_unknown_not_absence(tmp_path):
    root=pci_fixture(tmp_path,include_discrete=False)
    (root/"0000:05:00.0").mkdir()
    report=w.read_r9700_hardware(root)
    assert report["pci_inventory_usable"] is False
    assert report["r9700_pci_present"] is None


def test_empty_pci_device_directory_is_unknown(tmp_path):
    root=tmp_path/"pci"
    root.mkdir()
    report=w.read_r9700_hardware(root)
    assert report["pci_inventory_usable"] is False
    assert report["r9700_pci_present"] is None


def board_fixture(tmp_path, *, board="PRIME B650M-A AX II",
                  vendor="ASUSTeK COMPUTER INC.", bios="3014",
                  include_bridge=True):
    dmi=tmp_path/"dmi"
    pci=tmp_path/"pci"
    dmi.mkdir();pci.mkdir()
    (dmi/"board_name").write_text(board+"\n")
    (dmi/"board_vendor").write_text(vendor+"\n")
    (dmi/"bios_version").write_text(bios+"\n")
    (dmi/"bios_date").write_text("07/17/2024\n")
    d=pci/"0000:00:01.2"
    if include_bridge:
        d.mkdir()
        (d/"class").write_text("0x060400\n")
        (d/"vendor").write_text("0x1022\n")
        (d/"current_link_speed").write_text("32.0 GT/s PCIe\n")
        (d/"current_link_width").write_text("4\n")
        (d/"max_link_speed").write_text("32.0 GT/s PCIe\n")
        (d/"max_link_width").write_text("4\n")
    return dmi,pci


def test_board_pcie_context_allowlisted_board_and_bridge(tmp_path):
    dmi,pci=board_fixture(tmp_path)
    out=w.read_board_pcie_context(dmi,pci)
    assert out["target_board_verified"] is True
    assert out["board"] == "PRIME B650M-A AX II"
    assert out["bios_version"] == "3014"
    assert out["bios_date"] == "07/17/2024"
    assert out["pcie_bridge_inventory_usable"] is True
    assert out["amd_bridge_count"] == 1
    assert out["amd_bridges"] == [{
        "bdf":"0000:00:01.2",
        "link_speed":"32.0 GT/s PCIe",
        "link_width":4,
        "max_link_speed":"32.0 GT/s PCIe",
        "max_link_width":4,
    }]
    assert out["physical_r9700_slot_identified"] is False
    assert out["bios_update_authorized"] is False


def test_empty_bridge_inventory_is_unknown(tmp_path):
    dmi,pci=board_fixture(tmp_path,include_bridge=False)
    out=w.read_board_pcie_context(dmi,pci)
    assert out["pcie_bridge_inventory_usable"] is False
    assert out["amd_bridge_count"] is None
    assert out["bios_update_authorized"] is False


def test_unknown_board_does_not_invent_verified_asus_model(tmp_path):
    dmi,pci=board_fixture(tmp_path,board="Another Board")
    out=w.read_board_pcie_context(dmi,pci)
    assert out["board"] is None
    assert out["target_board_verified"] is False
    assert out["physical_r9700_slot_identified"] is False


def test_malformed_bdf_or_partial_bridge_census_fails_closed(tmp_path):
    dmi,pci=board_fixture(tmp_path)
    incomplete=pci/"not_a_bdf"
    incomplete.mkdir()
    (incomplete/"class").write_text("0x060400")
    (incomplete/"vendor").write_text("0x1022")
    out=w.read_board_pcie_context(dmi,pci)
    assert out["pcie_bridge_inventory_usable"] is False
    assert out["amd_bridge_count"] is None
    assert out["amd_bridges"] == []


def test_missing_pci_class_is_unverified_not_clean_inventory(tmp_path):
    dmi,pci=board_fixture(tmp_path)
    (pci/"0000:00:01.2"/"class").unlink()
    out=w.read_board_pcie_context(dmi,pci)
    assert out["pcie_bridge_inventory_usable"] is False


def test_invalid_link_metadata_redacted_without_fake_pass(tmp_path):
    dmi,pci=board_fixture(tmp_path)
    d=pci/"0000:00:01.2"
    (d/"current_link_speed").write_text("PRIVATE-KEY-DATA")
    (d/"current_link_width").write_text("-9")
    out=w.read_board_pcie_context(dmi,pci)
    assert out["pcie_bridge_inventory_usable"] is True
    assert out["amd_bridges"][0]["link_speed"] is None
    assert out["amd_bridges"][0]["link_width"] is None
    assert "PRIVATE-KEY-DATA" not in json.dumps(out)
    assert out["bios_update_authorized"] is False


def test_board_context_does_not_claim_graphics_slot_or_recovery(tmp_path):
    dmi,pci=board_fixture(tmp_path)
    context=w.read_board_pcie_context(dmi,pci)
    run,_,_=fixture_runner()
    before=w.capture(runner=run,health=lambda:None,boot=boot_record,
                     memory=lambda:{},hardware=lambda:{
                         "pci_inventory_usable": True, "r9700_pci_present": False
                     },board_context=lambda:context)
    result=w.evaluate(before,before,host_ok=True)
    assert result["verdict"] == "OUTAGE_HOLD"
    assert result["board_pcie"]["target_board_verified"]
    assert not result["board_pcie"]["physical_r9700_slot_identified"]
    assert not result["board_pcie"]["bios_update_authorized"]
    assert result["r9700_runtime_capability_verified"] is False


def test_pressure_reports_only_bounded_numeric_averages(tmp_path):
    for category, typ, avg in (
        ("memory", "some", "0.35"),
        ("cpu", "some", "1.50"),
        ("io", "full", "43.25"),
    ):
        (tmp_path/category).write_text(
            f"{typ} avg10={avg} avg60=100 avg300=25.0 total=5000\n"
        )
    report=w.read_pressure(tmp_path)
    assert report == {
        "memory_some_avg10": 0.35,
        "cpu_some_avg10": 1.5,
        "io_full_avg10": 43.25,
    }
    assert "total" not in str(report)


def test_incomplete_or_invalid_pressure_never_claims_zero(tmp_path):
    (tmp_path/"memory").write_text("some avg10=nan avg60=1 total=2\n")
    (tmp_path/"cpu").write_text("some avg10=SECRET-KEY avg60=0\n")
    (tmp_path/"io").write_text("full avg10=999 avg60=5\n")
    report=w.read_pressure(tmp_path)
    assert all(x is None for x in report.values())
    assert "SECRET" not in json.dumps(report)
    (tmp_path/"io").unlink()
    assert w.read_pressure(tmp_path)["io_full_avg10"] is None


def test_r9700_gtt_missing_does_not_invent_headroom(tmp_path):
    root=pci_fixture(tmp_path)
    (root/"0000:03:00.0"/"mem_info_gtt_total").unlink()
    report=w.read_r9700_hardware(root)
    assert report["r9700_pci_present"] is True
    assert report["r9700_gtt_free_mib"] is None


def test_r9700_gtt_overcommitted_not_negative_headroom(tmp_path):
    root=pci_fixture(tmp_path)
    d=root/"0000:03:00.0"
    (d/"mem_info_gtt_used").write_text(str(12*1024**3))
    report=w.read_r9700_hardware(root)
    assert report["r9700_gtt_free_mib"] is None


def test_pressure_is_reported_only_not_repair_authorization(tmp_path):
    context={"memory_some_avg10":0.0,"cpu_some_avg10":0.0,"io_full_avg10":44.0}
    runner,_,_=fixture_runner()
    before=w.capture(runner=runner,health=lambda:None,boot=boot_record,
                     memory=lambda:{},hardware=lambda:{},
                     board_context=lambda:{},pressure=lambda:context)
    result=w.evaluate(before,before,host_ok=True)
    assert result["pressure"]["io_full_avg10"] == 44.0
    assert result["service_change_authorized"] is False
    assert result["router_cutover_authorized"] is False
