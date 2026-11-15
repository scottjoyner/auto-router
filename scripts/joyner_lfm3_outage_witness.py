#!/usr/bin/env python3
"""Bounded, read-only Joyner LFM3 outage witness; always exits HOLD 78.

No ExecStart, Environment, credentials, unit-file contents, raw logs, process
command lines, response bodies or model requests are collected or emitted.
The only HTTP request is a local GET /health on 127.0.0.1:19175.
Do not use this receipt to change a service or GPU lease.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SCHEMA = "auto-router.joyner-lfm3-outage-readonly.v1"
HOST = "joyner"
UNITS = (
    "lfm3-joyner-inference",
    "kipnerter-lfm3-r9700-bridge",
    "joyner-lane-1234",
    "fleet-joyner-gpu",
    "fleet-joyner-ling",
)
PORTS = (1234, 1236, 19175)
ALLOWED_PROPERTIES = (
    "LoadState", "ActiveState", "SubState", "Result",
    "NRestarts", "ExecMainStatus", "ExecMainCode", "MainPID",
    "StateChangeTimestamp",
)
SAFE_VALUES = {
    "LoadState": {"loaded", "not-found", "masked", "error", "unverified"},
    "ActiveState": {"active", "inactive", "activating", "deactivating", "failed", "unverified"},
    "SubState": {"running", "dead", "auto-restart", "exited", "failed", "start", "unverified"},
    "Result": {"success", "exit-code", "signal", "timeout", "oom-kill", "unverified"},
}


def _run(args: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=5, check=False
        )
        return result.returncode, result.stdout[:40_000]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 1, ""


def _clean_numeric(text: str) -> int | None:
    return int(text) if text.isdecimal() and len(text) <= 15 else None


def parse_properties(raw: str) -> dict:
    found = {}
    for line in raw.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key not in ALLOWED_PROPERTIES:
            continue
        if key in SAFE_VALUES:
            found[key] = value if value in SAFE_VALUES[key] else "unverified"
        elif key in ("NRestarts", "ExecMainStatus", "ExecMainCode", "MainPID"):
            found[key] = _clean_numeric(value)
        elif key == "StateChangeTimestamp":
            # Timestamp may be systemd-formatted; never emit arbitrary text
            # from a unit field that could include injected values.
            found["state_change_reported"] = bool(value.strip())
    return found


def unit_snapshot(name: str, runner=_run) -> dict:
    if name not in UNITS:
        raise ValueError("unit_not_allowlisted")
    args = ["systemctl", "--user", "show", name + ".service", "--no-pager"]
    for key in ALLOWED_PROPERTIES:
        args.extend(("-p", key))
    rc, raw = runner(args)
    record = parse_properties(raw) if rc == 0 else {}
    return {
        "unit": name,
        "query_usable": rc == 0,
        "load": record.get("LoadState", "unverified"),
        "active": record.get("ActiveState", "unverified"),
        "sub": record.get("SubState", "unverified"),
        "result": record.get("Result", "unverified"),
        "restart_count": record.get("NRestarts"),
        "main_pid_present": bool(record.get("MainPID")),
        "last_exit_status": record.get("ExecMainStatus"),
        "state_change_reported": record.get("state_change_reported", False),
    }


def ports_snapshot(runner=_run) -> dict:
    rc, raw = runner(["ss", "-H", "-ltn"])
    if rc != 0:
        return {"socket_inventory_usable": False,
                "listening": {str(x): None for x in PORTS}}
    active = {x: False for x in PORTS}
    for line in raw.splitlines():
        cols = line.split()
        if len(cols) < 4 or cols[0] != "LISTEN":
            continue
        field = cols[3].rsplit(":", 1)
        if len(field) != 2:
            continue
        p = _clean_numeric(field[1])
        if p in active:
            active[p] = True
    return {"socket_inventory_usable": True,
            "listening": {str(x): active[x] for x in PORTS}}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_health() -> int | None:
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    req = Request("http://127.0.0.1:19175/health",
                  method="GET", headers={"Accept": "application/json"})
    try:
        with opener.open(req, timeout=2) as response:
            return response.status
    except HTTPError as exc:
        result = exc.code
        exc.close()
        return result
    except (URLError, OSError, TimeoutError, ValueError):
        return None


def _read_memory(path: Path = Path("/proc/meminfo")) -> dict:
    values = {}
    try:
        for line in path.read_text().splitlines():
            key, _, raw = line.partition(":")
            if key in ("MemAvailable", "SwapFree", "SwapTotal"):
                number = raw.strip().split()
                if len(number) == 2 and number[1] == "kB":
                    values[key + "_mib"] = int(number[0]) // 1024
    except (OSError, ValueError):
        pass
    return values


def _boot(path: Path = Path("/proc/sys/kernel/random/boot_id"),
          uptime: Path = Path("/proc/uptime")) -> dict:
    try:
        identity = path.read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", identity):
            raise ValueError("invalid_boot_id")
        seconds = float(uptime.read_text().split()[0])
        if not 0 <= seconds <= 365 * 24 * 3600:
            raise ValueError("invalid_uptime")
        return {"boot_id_sha256": hashlib.sha256(identity.encode()).hexdigest(),
                "uptime_seconds": int(seconds), "verified": True}
    except (OSError, ValueError, IndexError):
        return {"boot_id_sha256": None, "uptime_seconds": None,
                "verified": False}


def read_r9700_hardware(root: Path = Path("/sys/bus/pci/devices")) -> dict:
    """Read exactly AMD 1002:7551 PCI identity and driver/VRAM metadata.

    PCI bus/device locations can change across reboots: do not insist on
    historical BDF 0000:03:00.0. Absence is meaningful only when the
    sysfs device enumeration itself was accessible.
    """
    try:
        devices = list(root.iterdir())
    except OSError:
        return {
            "pci_inventory_usable": False,
            "r9700_pci_present": None,
            "r9700_amdgpu_bound": None,
            "r9700_vram_total_mib": None,
            "r9700_pci_location": None,
        }
    if not devices:
        return {"pci_inventory_usable": False,
                "r9700_pci_present": None, "r9700_unique_identity": False,
                "r9700_amdgpu_bound": None, "r9700_vram_total_mib": None,
                "r9700_pci_location": None}
    found = []
    unreadable = False
    for d in devices:
        try:
            if ((d / "vendor").read_text().strip().lower() == "0x1002"
                    and (d / "device").read_text().strip().lower() == "0x7551"):
                found.append(d)
        except OSError:
            unreadable = True
    if unreadable:
        return {"pci_inventory_usable": False,
                "r9700_pci_present": None, "r9700_unique_identity": False,
                "r9700_amdgpu_bound": None, "r9700_vram_total_mib": None,
                "r9700_pci_location": None}
    # One observed exact AMD vendor/device identity is required. Do not
    # invent a "usable GPU" verdict from an ambiguous or partial census.
    if len(found) != 1:
        return {
            "pci_inventory_usable": True,
            "r9700_pci_present": len(found) > 0,
            "r9700_unique_identity": False,
            "r9700_amdgpu_bound": None,
            "r9700_vram_total_mib": None,
            "r9700_pci_location": None,
        }
    d = found[0]
    try:
        bound = (d / "driver").is_symlink() and (d / "driver").resolve().name == "amdgpu"
    except OSError:
        bound = False
    try:
        bytes_total = int((d / "mem_info_vram_total").read_text().strip())
        mib = bytes_total // (1024 * 1024) if 0 < bytes_total < 2**50 else None
    except (OSError, ValueError):
        mib = None
    return {
        "pci_inventory_usable": True,
        "r9700_pci_present": True,
        "r9700_unique_identity": True,
        "r9700_amdgpu_bound": bound,
        "r9700_vram_total_mib": mib,
        "r9700_pci_location": d.name if re.fullmatch(
            r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", d.name
        ) else None,
    }


def read_board_pcie_context(
        dmi: Path = Path("/sys/class/dmi/id"),
        pci: Path = Path("/sys/bus/pci/devices")) -> dict:
    """Board identity and bounded bridge-link census; never read PCI config.

    Any partial PCI bridge census returns an unusable inventory rather than
    a fabricated missing-link diagnosis. A given bridge is NOT assumed to
    be the physical graphics slot.
    """
    def text_field(name: str) -> str | None:
        try:
            raw = (dmi / name).read_text().strip()
            if not 1 <= len(raw) <= 64 or not re.fullmatch(
                    r"[A-Za-z0-9 ._+/@-]+", raw):
                return None
            return raw
        except OSError:
            return None

    board = text_field("board_name")
    vendor = text_field("board_vendor")
    bios = text_field("bios_version")
    bios_date = text_field("bios_date")
    target_board = board == "PRIME B650M-A AX II" and (
        vendor == "ASUSTeK COMPUTER INC."
    )
    if bios is not None and not re.fullmatch(r"[0-9]{3,5}", bios):
        bios = None
    try:
        devices = list(pci.iterdir())
        if not devices:
            raise ValueError("empty_pci_inventory")
    except (OSError, ValueError):
        return {
            "board": board if target_board else None,
            "target_board_verified": target_board,
            "bios_version": bios,
            "bios_date": bios_date,
            "pcie_bridge_inventory_usable": False,
            "amd_bridge_count": None,
            "amd_bridges": [],
            "physical_r9700_slot_identified": False,
            "bios_update_authorized": False,
        }

    bridges = []
    invalid = False
    bdf_pattern = r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]"
    for device in devices:
        try:
            klass = (device / "class").read_text().strip().lower()
            if klass != "0x060400":
                continue
            vendor_id = (device / "vendor").read_text().strip().lower()
            if vendor_id != "0x1022":
                continue
            if not re.fullmatch(bdf_pattern, device.name):
                invalid = True
                continue
            speeds = {"2.5 GT/s PCIe", "5.0 GT/s PCIe",
                      "8.0 GT/s PCIe", "16.0 GT/s PCIe",
                      "32.0 GT/s PCIe", "64.0 GT/s PCIe"}
            def speed(name: str) -> str | None:
                try:
                    v = (device / name).read_text().strip()
                    return v if v in speeds else None
                except OSError:
                    return None
            def width(name: str) -> int | None:
                try:
                    v = int((device / name).read_text().strip())
                    return v if 1 <= v <= 32 else None
                except (OSError, ValueError):
                    return None
            bridges.append({
                "bdf": device.name,
                "link_speed": speed("current_link_speed"),
                "link_width": width("current_link_width"),
                "max_link_speed": speed("max_link_speed"),
                "max_link_width": width("max_link_width"),
            })
        except OSError:
            invalid = True
    bridges.sort(key=lambda b: b["bdf"])
    # This is a snapshot of enumerated bridges, NOT a physical slot map.
    # A root port may be absent when no downstream endpoint enumerates.
    return {
        "board": board if target_board else None,
        "target_board_verified": target_board,
        "bios_version": bios,
        "bios_date": bios_date,
        "pcie_bridge_inventory_usable": not invalid,
        "amd_bridge_count": len(bridges) if not invalid else None,
        "amd_bridges": bridges if not invalid else [],
        "physical_r9700_slot_identified": False,
        "bios_update_authorized": False,
    }


def capture(runner=_run, health=local_health,
            memory=_read_memory, boot=_boot,
            hardware=read_r9700_hardware,
            board_context=read_board_pcie_context) -> dict:
    """One read-only bounded snapshot. No hidden writes or network targets."""
    return {
        "boot": boot(), "units": [unit_snapshot(name, runner) for name in UNITS],
        "ports": ports_snapshot(runner),
        "local_lfm3_health_http": health(),
        "resources": memory(), "r9700_hardware": hardware(),
        "board_pcie": board_context(),
    }


def evaluate(before: dict, after: dict, *, host_ok: bool) -> dict:
    b = {u["unit"]: u for u in before["units"]}
    a = {u["unit"]: u for u in after["units"]}
    changes = []
    for name in UNITS:
        old, new = b.get(name, {}), a.get(name, {})
        x, y = old.get("restart_count"), new.get("restart_count")
        # A counter reset or reboot is not evidence of repair; keep HOLD.
        delta = y - x if isinstance(x, int) and isinstance(y, int) and y >= x else None
        changes.append({
            "unit": name, "restart_count_first": x,
            "restart_count_last": y, "restart_count_delta": delta,
            "last_state": new.get("active", "unverified"),
            "last_substate": new.get("sub", "unverified"),
        })
    same_boot = (
        before["boot"].get("verified") is True
        and after["boot"].get("verified") is True
        and before["boot"].get("boot_id_sha256") ==
            after["boot"].get("boot_id_sha256")
    )
    observable_outage = (
        a.get("lfm3-joyner-inference", {}).get("active") != "active"
        or after["ports"]["listening"].get("19175") is not True
        or after["local_lfm3_health_http"] != 200
    )
    return {
        "schema": SCHEMA, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "observer_host_verified": host_ok,
        "verdict": "OUTAGE_HOLD" if host_ok and observable_outage else "HOLD",
        "exit_code": 78, "read_only": True,
        "same_boot_during_observation": same_boot,
        "boot": after["boot"], "restarts": changes,
        "final_units": after["units"], "final_ports": after["ports"],
        "local_lfm3_health_http": after["local_lfm3_health_http"],
        "resources": after["resources"],
        "r9700_hardware": after.get("r9700_hardware", {}),
        "board_pcie": after.get("board_pcie", {}),
        "same_pci_identity_during_observation": (
            before.get("r9700_hardware") == after.get("r9700_hardware")
            and before.get("r9700_hardware", {}).get("pci_inventory_usable") is True
        ),
        "r9700_runtime_capability_verified": False,
        "lfm3_failure_attributed_to_r9700_loss": False,
        "log_root_cause_verified": False,
        "protected_key_read": False, "model_request_sent": False,
        "model_generation_performed": False,
        "unit_start_stop_restart_performed": False,
        "service_change_authorized": False, "lease_change_authorized": False,
        "router_cutover_authorized": False,
    }


def audit(*, hostname: str | None = None, runner=_run,
          health=local_health, memory=_read_memory, boot=_boot,
          hardware=read_r9700_hardware,
          board_context=read_board_pcie_context,
          sleep=time.sleep) -> dict:
    host_ok = (hostname or socket.gethostname()).split(".", 1)[0].lower() == HOST
    if not host_ok:
        return evaluate(
            {"units": [], "boot": {"verified": False}},
            {"units": [], "boot": {"verified": False},
             "ports": {"socket_inventory_usable": False, "listening": {}},
             "local_lfm3_health_http": None, "resources": {},
             "r9700_hardware": {"pci_inventory_usable": False},
             "board_pcie": {"target_board_verified": False}},
            host_ok=False,
        )
    before = capture(runner, health, memory, boot, hardware, board_context)
    sleep(2)
    after = capture(runner, health, memory, boot, hardware, board_context)
    return evaluate(before, after, host_ok=True)


def main() -> int:
    print(json.dumps(audit(), sort_keys=True, separators=(",", ":")))
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
