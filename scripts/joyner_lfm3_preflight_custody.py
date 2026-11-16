#!/usr/bin/env python3
"""Joyner LFM3 fail-closed *source* and startup-preflight custody witness.

Only reads allowlisted user systemd unit properties and the existing,
same-owner Python preflight source. Never executes source, reads key files,
model bytes, logs or arbitrary URLs. Never changes service or host state.
HOLD (exit 78) is the only outcome.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shlex
import socket
import stat
import subprocess
from pathlib import Path
from typing import Callable

SCHEMA = "auto-router.joyner-lfm3-preflight-custody.v1"
SOURCE_SHA256 = "ab39f744258023a91464ba53815c63092cf0d0fb5a143eff54861eda92f1f685"
LFM3_COMMAND_SHA256 = "37ab7ab3a6d462326be9ebf3d30c071f6a0c49581361d7d3a331cf15f0735de6"
BRIDGE_COMMAND_SHA256 = "a3c6b332208afc0ec506d49f3912ceff747bb91df401991a85406db90f200787"
SOURCE_NAME = "server_preflight.py"
EXPECTED_VRAM_BYTES = 30 * (2 ** 30)
EXPECTED_MEMAVAILABLE_MIB = 2200
ALLOWED_UNITS = (
    "lfm3-joyner-inference",
    "kipnerter-lfm3-r9700-bridge",
)


def _run(argv: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=5, check=False
        )
        return result.returncode, result.stdout[:32_768]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return 1, ""


def _preflight_is_failing(raw: str) -> bool | None:
    line = next((x for x in raw.splitlines()
                 if x.startswith("ExecStartPre=")), None)
    if line is None:
        return None
    statuses = re.findall(r"\bcode=exited\s*;\s*status=(\d+)\b", line)
    if len(statuses) != 1:
        return None
    return statuses[0] != "0"


def _script_is_trusted(path: Path, home: Path, uid: int) -> bool:
    if (not path.is_absolute() or ".." in path.parts or path.name != SOURCE_NAME
            or not path.is_relative_to(home) or path == home):
        return False
    for part in (path, *path.parents):
        if part == home:
            break
        if part.is_symlink():
            return False
    try:
        st = path.stat()
        return stat.S_ISREG(st.st_mode) and st.st_uid == uid and st.st_size <= 8192
    except OSError:
        return False


def _script_contract(path: Path, home: Path, uid: int) -> dict:
    safe = {"known_preflight_source_sha256_verified": False,
            "preflight_source_owned_by_observer": False,
            "gpu_vram_30gib_guard_pinned": False,
            "system_ram_2200mib_guard_pinned": False,
            "model_integrity_guard_pinned": False,
            "keyfile_metadata_guard_pinned": False}
    if not _script_is_trusted(path, home, uid):
        return safe
    safe["preflight_source_owned_by_observer"] = True
    try:
        raw = path.read_bytes()
        tree = ast.parse(raw)
        digest_ok = hashlib.sha256(raw).hexdigest() == SOURCE_SHA256
        safe["known_preflight_source_sha256_verified"] = digest_ok
        if digest_ok and isinstance(tree, ast.Module):
            # This is a pinned, previously audited source digest:
            # these properties are bound to that digest, not inferred
            # from untrusted regex snippets or an executable callback.
            for key in (
                "gpu_vram_30gib_guard_pinned",
                "system_ram_2200mib_guard_pinned",
                "model_integrity_guard_pinned",
                "keyfile_metadata_guard_pinned",
            ):
                safe[key] = True
    except (OSError, ValueError, SyntaxError):
        pass
    return safe


def _preflight_lines(raw: str) -> list[str]:
    return [x.partition("=")[2] for x in raw.splitlines()
            if x.startswith("ExecStartPre=")]


def _python_path(command: str) -> Path | None:
    try:
        args = shlex.split(command)
    except ValueError:
        return None
    if (len(args) != 2 or
            Path(args[0]).name not in ("python3", "python3.12", "python")):
        return None
    path = Path(args[1])
    return path if path.is_absolute() and path.name == SOURCE_NAME else None


def _bridge_health_guard(command: str) -> bool:
    # Parse source string, never invoke curl. This is *not* a proof of
    # authenticated bridge behavior, only a dependency on LFM3 health.
    return ("curl" in command and "127.0.0.1:19175/health" in command
            and bool(re.search(r"(?:\s|^)(?:-f[a-zA-Z]*|--fail)(?:\s|$)", command)))


def _gpu_present(root: Path = Path("/sys/bus/pci/devices")) -> bool | None:
    try:
        found = []
        devices = list(root.iterdir())
        if not devices:
            return None
        for item in devices:
            if ((item / "vendor").read_text().strip().lower() == "0x1002"
                    and (item / "device").read_text().strip().lower() == "0x7551"):
                found.append(item)
        return bool(found)
    except OSError:
        return None


def _ram_available(path: Path = Path("/proc/meminfo")) -> int | None:
    try:
        rows = path.read_text().splitlines()
        matches = [row.split() for row in rows if row.startswith("MemAvailable:")]
        if (len(matches) != 1 or len(matches[0]) != 3
                or matches[0][-1] != "kB"):
            return None
        number = int(matches[0][1])
        return number // 1024 if number >= 0 else None
    except (OSError, ValueError):
        return None


def audit(*, hostname: str | None = None, runner: Callable = _run,
          home: Path | None = None, uid: int | None = None,
          gpu_reader: Callable = _gpu_present,
          ram_reader: Callable = _ram_available) -> dict:
    host_ok = (hostname or socket.gethostname()).split(".", 1)[0].lower() == "joyner"
    report = {
        "schema": SCHEMA, "verdict": "HOLD", "exit_code": 78,
        "read_only": True, "observer_host_verified": host_ok,
        "pinned_preflight_contract": {},
        "bridge_local_lfm3_health_dependency_verified": False,
        "lfm3_preflight_launch_command_pinned": False,
        "bridge_preflight_launch_command_pinned": False,
        "lfm3_preflight_exit_nonzero_observed": None,
        "bridge_preflight_exit_nonzero_observed": None,
        "r9700_pci_present": None,
        "memavailable_mib": None,
        "vram_guard_would_refuse_if_reached": False,
        "ram_guard_would_refuse_if_reached": False,
        "upstream_lfm3_health_dependency_is_not_release_evidence": True,
        "key_file_opened": False, "model_file_opened": False,
        "preflight_executed": False, "model_request_sent": False,
        "service_change_authorized": False, "router_cutover_authorized": False,
    }
    if not host_ok:
        return report
    actual_home = home if home is not None else Path.home()
    actual_uid = os.geteuid() if uid is None else uid
    for service in ALLOWED_UNITS:
        rc, unit_raw = runner(
            ["systemctl", "--user", "cat", service + ".service"])
        status_rc, status_raw = runner([
            "systemctl", "--user", "show", service + ".service",
            "--no-pager", "-p", "ExecStartPre",
        ])
        if service == "lfm3-joyner-inference":
            report["lfm3_preflight_exit_nonzero_observed"] = (
                _preflight_is_failing(status_raw) if status_rc == 0 else None
            )
            preflights = _preflight_lines(unit_raw) if rc == 0 else []
            if len(preflights) == 1:
                exact = (
                    hashlib.sha256(preflights[0].encode()).hexdigest()
                    == LFM3_COMMAND_SHA256
                )
                report["lfm3_preflight_launch_command_pinned"] = exact
                path = _python_path(preflights[0]) if exact else None
                if path is not None:
                    report["pinned_preflight_contract"] = _script_contract(
                        path, actual_home, actual_uid)
        else:
            report["bridge_preflight_exit_nonzero_observed"] = (
                _preflight_is_failing(status_raw) if status_rc == 0 else None
            )
            preflights = _preflight_lines(unit_raw) if rc == 0 else []
            if len(preflights) == 1:
                exact = (
                    hashlib.sha256(preflights[0].encode()).hexdigest()
                    == BRIDGE_COMMAND_SHA256
                )
                report["bridge_preflight_launch_command_pinned"] = exact
                report["bridge_local_lfm3_health_dependency_verified"] = (
                    exact and _bridge_health_guard(preflights[0])
                )
    report["r9700_pci_present"] = gpu_reader()
    report["memavailable_mib"] = ram_reader()
    contract = report["pinned_preflight_contract"]
    if contract.get("known_preflight_source_sha256_verified") is True:
        report["vram_guard_would_refuse_if_reached"] = (
            report["r9700_pci_present"] is False)
        ram = report["memavailable_mib"]
        report["ram_guard_would_refuse_if_reached"] = (
            type(ram) is int and ram < EXPECTED_MEMAVAILABLE_MIB)
    return report


def main() -> int:
    print(json.dumps(audit(), sort_keys=True, separators=(",", ":")))
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
