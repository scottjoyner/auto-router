#!/usr/bin/env python3
"""Evaluate an already-created Joyner outage receipt. No host operations.

Hardware reappearance is a candidate for manual review, not permission to
restart models, change routing or install drivers. JSON is untrusted input:
emit only fixed classifications/reasons, no raw fields or host identifiers.
Always return HOLD (exit 78), even when every factual gate is green.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

SOURCE_SCHEMA = "auto-router.joyner-lfm3-outage-readonly.v1"
SCHEMA = "auto-router.joyner-r9700-maintenance-assessment.v1"
MIN_R9700_VRAM_MIB = 30_000
MAX_AGE_SECONDS = 900
REQUIRED_UNITS = (
    "lfm3-joyner-inference",
    "kipnerter-lfm3-r9700-bridge",
    "fleet-joyner-gpu",
    "fleet-joyner-ling",
)


def _record(value: Any) -> dict:
    return value if type(value) is dict else {}


def _valid_time(value: Any, now: datetime) -> bool:
    if type(value) is not str:
        return False
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return False
        age = (now - stamp).total_seconds()
        return -120 <= age <= MAX_AGE_SECONDS
    except (TypeError, ValueError, OverflowError):
        return False


def classify(report: Any, *, now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    d = _record(report)
    reasons: list[str] = []
    if d.get("schema") != SOURCE_SCHEMA or d.get("exit_code") != 78:
        reasons.append("SOURCE_SCHEMA_OR_HOLD_INVALID")
    if d.get("observer_host_verified") is not True:
        reasons.append("JOYNER_OBSERVER_UNVERIFIED")
    if not _valid_time(d.get("captured_at_utc"), current):
        reasons.append("SOURCE_RECEIPT_NOT_FRESH")
    boot = _record(d.get("boot"))
    if (d.get("same_boot_during_observation") is not True
            or boot.get("verified") is not True):
        reasons.append("BOOT_CONTINUITY_UNVERIFIED")
    board = _record(d.get("board_pcie"))
    if (board.get("target_board_verified") is not True
            or board.get("board") != "PRIME B650M-A AX II"):
        reasons.append("TARGET_MOTHERBOARD_UNVERIFIED")
    if board.get("bios_update_authorized") is not False:
        reasons.append("BIOS_UPDATE_AUTHORITY_UNVERIFIED")
    hw = _record(d.get("r9700_hardware"))
    if (hw.get("pci_inventory_usable") is not True
            or d.get("same_pci_identity_during_observation") is not True):
        reasons.append("PCI_INVENTORY_OR_STABILITY_UNVERIFIED")
    if hw.get("r9700_unique_identity") is not True:
        reasons.append("R9700_UNIQUE_IDENTITY_UNVERIFIED")
    if hw.get("r9700_pci_present") is not True:
        reasons.append("R9700_PCI_NOT_PRESENT")
    if hw.get("r9700_amdgpu_bound") is not True:
        reasons.append("R9700_DRIVER_NOT_VERIFIED")
    vram = hw.get("r9700_vram_total_mib")
    if type(vram) is not int or not MIN_R9700_VRAM_MIB <= vram <= 36_000:
        reasons.append("R9700_DISCRETE_VRAM_NOT_VERIFIED")
    ports = _record(_record(d.get("final_ports")).get("listening"))
    if ports.get("19175") is not True:
        reasons.append("LFM3_19175_NOT_LISTENING")
    if ports.get("1234") is not True or ports.get("1236") is not True:
        reasons.append("CANONICAL_MODEL_PORTS_UNVERIFIED")
    if d.get("local_lfm3_health_http") != 200:
        reasons.append("LFM3_HEALTH_NOT_VERIFIED")
    units = d.get("final_units")
    if type(units) is not list:
        units = []
    by_name = {}
    for unit in units:
        row = _record(unit)
        name = row.get("unit")
        if name in REQUIRED_UNITS:
            if name in by_name:
                reasons.append("DUPLICATE_SERVICE_EVIDENCE")
            by_name[name] = row
    restarts = d.get("restarts")
    if type(restarts) is not list:
        restarts = []
    by_restart = {}
    for item in restarts:
        row = _record(item)
        name = row.get("unit")
        if name in REQUIRED_UNITS:
            if name in by_restart:
                reasons.append("DUPLICATE_RESTART_EVIDENCE")
            by_restart[name] = row
    for name in REQUIRED_UNITS:
        unit = by_name.get(name)
        if not unit or unit.get("query_usable") is not True or (
                unit.get("active") != "active"
                or unit.get("sub") != "running"):
            reasons.append("REQUIRED_SERVICE_UNVERIFIED")
            break
    for name in REQUIRED_UNITS:
        item = by_restart.get(name)
        if (not item or type(item.get("restart_count_delta")) is not int
                or item["restart_count_delta"] != 0):
            reasons.append("RESTART_STABILITY_UNVERIFIED")
            break
    if (d.get("service_change_authorized") is not False
            or d.get("router_cutover_authorized") is not False
            or d.get("lease_change_authorized") is not False
            or d.get("protected_key_read") is not False
            or d.get("unit_start_stop_restart_performed") is not False):
        reasons.append("SOURCE_SAFETY_CONTRACT_UNVERIFIED")
    ordered = sorted(set(reasons))
    return {
        "schema": SCHEMA,
        "verdict": "HOLD",
        "exit_code": 78,
        "hardware_and_services_candidate_for_human_review": not ordered,
        "reasons": ordered,
        "read_only": True,
        "evidence_signature_verified": False,
        "physical_power_slot_inspection_verified": False,
        "resident_model_weights_verified": False,
        "synthetic_inference_contract_verified": False,
        "ingress_auth_policy_verified": False,
        "operator_maintenance_approval_verified": False,
        "production_route_admission_authorized": False,
        "bios_update_authorized": False,
        "service_restart_authorized": False,
        "gpu_lease_authorized": False,
    }


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(256 * 1024 + 1)
        source = json.loads(raw) if len(raw) <= 256 * 1024 else None
    except (OSError, ValueError, UnicodeDecodeError):
        source = None
    print(json.dumps(classify(source), sort_keys=True, separators=(",", ":")))
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
