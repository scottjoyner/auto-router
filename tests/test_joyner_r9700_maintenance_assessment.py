"""Offline acceptance for a *reported* GPU recovery. Never approves operations."""
from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from copy import deepcopy

import pytest

from scripts import joyner_r9700_maintenance_assessment as m

NOW = datetime(2026, 11, 15, 23, 35, tzinfo=timezone.utc)


def evidence():
    required = m.REQUIRED_UNITS
    return {
        "schema": m.SOURCE_SCHEMA,
        "exit_code": 78,
        "observer_host_verified": True,
        "captured_at_utc": (NOW - timedelta(seconds=15)).isoformat(),
        "boot": {"verified": True, "boot_id_sha256": "a" * 64},
        "same_boot_during_observation": True,
        "board_pcie": {
            "target_board_verified": True,
            "board": "PRIME B650M-A AX II",
            "bios_update_authorized": False,
        },
        "r9700_hardware": {
            "pci_inventory_usable": True,
            "r9700_pci_present": True,
            "r9700_unique_identity": True,
            "r9700_amdgpu_bound": True,
            "r9700_vram_total_mib": 32768,
            "r9700_gtt_free_mib": 4096,
            "r9700_pci_location": "0000:05:00.0",
        },
        "same_pci_identity_during_observation": True,
        "resources": {"MemAvailable_mib": 12288, "SwapFree_mib": 2048},
        "pressure": {
            "memory_some_avg10": 0.0,
            "cpu_some_avg10": 0.0,
            "io_full_avg10": 0.0,
        },
        "final_ports": {"listening": {
            "1234": True, "1236": True, "19175": True,
        }},
        "local_lfm3_health_http": 200,
        "final_units": [
            {"unit": n, "query_usable": True,
             "active": "active", "sub": "running"} for n in required
        ],
        "restarts": [
            {"unit": n, "restart_count_delta": 0} for n in required
        ],
        "service_change_authorized": False,
        "router_cutover_authorized": False,
        "lease_change_authorized": False,
        "protected_key_read": False,
        "unit_start_stop_restart_performed": False,
    }


def test_fully_healthy_fake_receipt_only_yields_candidate_hold():
    result = m.classify(evidence(), now=NOW)
    assert result["verdict"] == "HOLD"
    assert result["exit_code"] == 78
    assert result["hardware_and_services_candidate_for_human_review"] is True
    assert result["reasons"] == []
    assert all(result[key] is False for key in (
        "evidence_signature_verified",
        "physical_power_slot_inspection_verified",
        "resident_model_weights_verified",
        "synthetic_inference_contract_verified",
        "ingress_auth_policy_verified",
        "operator_maintenance_approval_verified",
        "production_route_admission_authorized",
        "bios_update_authorized",
        "service_restart_authorized",
        "gpu_lease_authorized",
    ))


@pytest.mark.parametrize("key,value,reason", [
    ("observer_host_verified", False, "JOYNER_OBSERVER_UNVERIFIED"),
    ("same_boot_during_observation", False, "BOOT_CONTINUITY_UNVERIFIED"),
    ("same_pci_identity_during_observation", False, "PCI_INVENTORY_OR_STABILITY_UNVERIFIED"),
    ("local_lfm3_health_http", None, "LFM3_HEALTH_NOT_VERIFIED"),
    ("protected_key_read", True, "SOURCE_SAFETY_CONTRACT_UNVERIFIED"),
    ("service_change_authorized", True, "SOURCE_SAFETY_CONTRACT_UNVERIFIED"),
    ("exit_code", 0, "SOURCE_SCHEMA_OR_HOLD_INVALID"),
    ("schema", "wrong-schema", "SOURCE_SCHEMA_OR_HOLD_INVALID"),
])
def test_bad_evidence_can_never_release(key,value,reason):
    source=evidence()
    source[key]=value
    result=m.classify(source,now=NOW)
    assert reason in result["reasons"]
    assert result["hardware_and_services_candidate_for_human_review"] is False
    assert result["production_route_admission_authorized"] is False


@pytest.mark.parametrize("field,value,reason", [
    ("pci_inventory_usable",False,"PCI_INVENTORY_OR_STABILITY_UNVERIFIED"),
    ("r9700_pci_present",False,"R9700_PCI_NOT_PRESENT"),
    ("r9700_unique_identity",False,"R9700_UNIQUE_IDENTITY_UNVERIFIED"),
    ("r9700_amdgpu_bound",False,"R9700_DRIVER_NOT_VERIFIED"),
    ("r9700_vram_total_mib",512,"R9700_DISCRETE_VRAM_NOT_VERIFIED"),
    ("r9700_vram_total_mib","32768","R9700_DISCRETE_VRAM_NOT_VERIFIED"),
])
def test_pci_hardware_gate_does_not_accept_missing_or_incomplete(field,value,reason):
    source=evidence()
    source["r9700_hardware"][field]=value
    result=m.classify(source,now=NOW)
    assert reason in result["reasons"]
    assert not result["hardware_and_services_candidate_for_human_review"]


def test_current_real_incident_safely_classified():
    source=evidence()
    source["r9700_hardware"]["r9700_pci_present"]=False
    source["r9700_hardware"]["r9700_unique_identity"]=False
    source["r9700_hardware"]["r9700_amdgpu_bound"]=None
    source["r9700_hardware"]["r9700_vram_total_mib"]=None
    source["final_ports"]["listening"]["19175"]=False
    source["local_lfm3_health_http"]=None
    source["final_units"][0]["active"]="activating"
    source["final_units"][0]["sub"]="auto-restart"
    source["restarts"][0]["restart_count_delta"]=2
    result=m.classify(source,now=NOW)
    assert result["verdict"]=="HOLD"
    assert "R9700_PCI_NOT_PRESENT" in result["reasons"]
    assert "LFM3_HEALTH_NOT_VERIFIED" in result["reasons"]
    assert "RESTART_STABILITY_UNVERIFIED" in result["reasons"]
    assert not result["hardware_and_services_candidate_for_human_review"]


def test_missing_or_duplicate_service_readout_denied():
    source=evidence()
    source["final_units"].append(deepcopy(source["final_units"][0]))
    r=m.classify(source,now=NOW)
    assert "DUPLICATE_SERVICE_EVIDENCE" in r["reasons"]
    source=evidence()
    source["restarts"].pop()
    r=m.classify(source,now=NOW)
    assert "RESTART_STABILITY_UNVERIFIED" in r["reasons"]


def test_false_port_success_does_not_replace_real_http_status():
    source=evidence()
    source["final_ports"]["listening"]["19175"]="true"
    r=m.classify(source,now=NOW)
    assert "LFM3_19175_NOT_LISTENING" in r["reasons"]
    source=evidence()
    source["local_lfm3_health_http"]=401
    r=m.classify(source,now=NOW)
    assert "LFM3_HEALTH_NOT_VERIFIED" in r["reasons"]


@pytest.mark.parametrize("delta",[None,-1,1,"0",True])
def test_growth_or_ambiguous_restart_counter_not_stability(delta):
    source=evidence()
    source["restarts"][0]["restart_count_delta"]=delta
    r=m.classify(source,now=NOW)
    assert "RESTART_STABILITY_UNVERIFIED" in r["reasons"]


def test_stale_future_or_naive_timestamp_not_admitted():
    for value in (
        (NOW - timedelta(hours=4)).isoformat(),
        (NOW + timedelta(minutes=15)).isoformat(),
        "2026-11-15T23:00:00",
        "PRIVATE_TOKEN_EXPLOSION",
    ):
        source=evidence()
        source["captured_at_utc"]=value
        r=m.classify(source,now=NOW)
        assert "SOURCE_RECEIPT_NOT_FRESH" in r["reasons"]
        assert value not in json.dumps(r)


def test_invalid_and_secret_attacker_input_not_emitted():
    for data in (None, "PRIVATE-PASSWORD", {"secret": "SYNTHETIC-SECRET"}, [1,2,3]):
        result=m.classify(data,now=NOW)
        assert result["verdict"]=="HOLD"
        assert result["reasons"]
        raw=json.dumps(result)
        assert "PRIVATE-PASSWORD" not in raw
        assert "SYNTHETIC-SECRET" not in raw
        assert result["operator_maintenance_approval_verified"] is False


def test_main_stdin_only_bounded_input_always_holds(monkeypatch,capsys):
    source=evidence()
    source["captured_at_utc"]=datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(m.sys,"stdin",
                        type("FakeStdin",(),{"buffer":io.BytesIO(json.dumps(source).encode())})())
    assert m.main()==78
    receipt=json.loads(capsys.readouterr().out)
    assert receipt["verdict"]=="HOLD"
    assert receipt["hardware_and_services_candidate_for_human_review"]
    assert not receipt["gpu_lease_authorized"]


def test_main_rejects_oversized_json(monkeypatch,capsys):
    payload=b"X"*(256*1024+5)
    monkeypatch.setattr(m.sys,"stdin",
                        type("FakeStdin",(),{"buffer":io.BytesIO(payload)})())
    assert m.main()==78
    receipt=json.loads(capsys.readouterr().out)
    assert receipt["verdict"]=="HOLD"
    assert receipt["reasons"]


@pytest.mark.parametrize("field,value,reason", [
    ("MemAvailable_mib", 460, "SYSTEM_RAM_HEADROOM_INSUFFICIENT_OR_UNKNOWN"),
    ("MemAvailable_mib", None, "SYSTEM_RAM_HEADROOM_INSUFFICIENT_OR_UNKNOWN"),
    ("MemAvailable_mib", "12288", "SYSTEM_RAM_HEADROOM_INSUFFICIENT_OR_UNKNOWN"),
    ("MemAvailable_mib", True, "SYSTEM_RAM_HEADROOM_INSUFFICIENT_OR_UNKNOWN"),
    ("SwapFree_mib", 900, "SWAP_HEADROOM_INSUFFICIENT_OR_UNKNOWN"),
    ("SwapFree_mib", None, "SWAP_HEADROOM_INSUFFICIENT_OR_UNKNOWN"),
])
def test_low_missing_or_untrusted_system_resources_fail_closed(field,value,reason):
    source=evidence()
    source["resources"][field]=value
    result=m.classify(source,now=NOW)
    assert reason in result["reasons"]
    assert result["hardware_and_services_candidate_for_human_review"] is False


@pytest.mark.parametrize("gtt", [None,-1,0,1024,"4096",True])
def test_gtt_headroom_unverified_or_low_blocks_review(gtt):
    source=evidence()
    source["r9700_hardware"]["r9700_gtt_free_mib"]=gtt
    result=m.classify(source,now=NOW)
    assert "R9700_GTT_HEADROOM_INSUFFICIENT_OR_UNKNOWN" in result["reasons"]
    assert not result["hardware_and_services_candidate_for_human_review"]


@pytest.mark.parametrize("key,value,reason", [
    ("memory_some_avg10",1.01,"MEMORY_PRESSURE_TOO_HIGH_OR_UNKNOWN"),
    ("memory_some_avg10",None,"MEMORY_PRESSURE_TOO_HIGH_OR_UNKNOWN"),
    ("cpu_some_avg10",2.01,"CPU_PRESSURE_TOO_HIGH_OR_UNKNOWN"),
    ("cpu_some_avg10","1.0","CPU_PRESSURE_TOO_HIGH_OR_UNKNOWN"),
    ("io_full_avg10",40.0,"IO_PRESSURE_TOO_HIGH_OR_UNKNOWN"),
    ("io_full_avg10",float("nan"),"IO_PRESSURE_TOO_HIGH_OR_UNKNOWN"),
    ("io_full_avg10",True,"IO_PRESSURE_TOO_HIGH_OR_UNKNOWN"),
])
def test_psi_pressure_cannot_be_ignored_or_forged(key,value,reason):
    source=evidence()
    source["pressure"][key]=value
    result=m.classify(source,now=NOW)
    assert reason in result["reasons"]
    assert result["verdict"]=="HOLD"
    assert not result["hardware_and_services_candidate_for_human_review"]


def test_specific_live_resource_pattern_blocks_review_even_if_otherwise_healthy():
    source=evidence()
    source["resources"]={"MemAvailable_mib":460,"SwapFree_mib":3502}
    source["pressure"]={
       "memory_some_avg10":0.0, "cpu_some_avg10":0.0,
       "io_full_avg10":42.75,
    }
    result=m.classify(source,now=NOW)
    assert "SYSTEM_RAM_HEADROOM_INSUFFICIENT_OR_UNKNOWN" in result["reasons"]
    assert "IO_PRESSURE_TOO_HIGH_OR_UNKNOWN" in result["reasons"]
    assert result["evidence_signature_verified"] is False
    assert result["service_restart_authorized"] is False
