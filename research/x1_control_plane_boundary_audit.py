#!/usr/bin/env python3
"""Read-only audit: MCP connectivity/containment is not OS recovery authorization.

Never obtains credentials, elevates privileges, restarts services or changes
production routes. A passing service check is NOT a boot recovery guarantee.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

UNITS = (
    "openai-mcp-desktop-commander.service",
    "fleet-exec-relay.service",
    "fleet-exec-device@x1-370.service",
)
PROPERTIES = ("ActiveState", "SubState", "NoNewPrivileges", "NRestarts", "Result")


def parse_unit_properties(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in PROPERTIES and key not in result:
            result[key] = value.strip()
    return result


def evaluate(
    units: dict[str, dict[str, str]],
    session_no_new_privs: bool,
) -> dict[str, Any]:
    checks: dict[str, str] = {}
    holds: list[str] = []
    for unit in UNITS:
        p = units.get(unit, {})
        active = p.get("ActiveState") == "active" and p.get("SubState") == "running"
        contained = p.get("NoNewPrivileges") == "yes"
        healthy = active and contained and p.get("Result") == "success"
        checks[unit] = (
            "active_and_sandboxed"
            if healthy
            else ("inactive_or_failed" if not active else "unexpected_sandbox_or_result")
        )
        if not healthy:
            holds.append("unit_unverified_or_unhealthy:" + unit)
    if not session_no_new_privs:
        holds.append("current_tool_execution_boundary_unverified")
    return {
        "scope": "read_only_fleet_commander_control_boundary",
        "mcp_and_fleet_exec_service_checks_pass": not holds,
        "service_findings": checks,
        "session_no_new_privs": session_no_new_privs,
        "privileged_recovery_authorized": False,
        "may_run_sudo": False,
        "may_repair_dpkg": False,
        "may_restart_services": False,
        "may_modify_fleet_routes": False,
        "machine_agent_task_auth_proven": False,
        "offline_console_and_rollback_proven": False,
        "issues": sorted(holds),
        "notes": [
            "Unit liveness does not prove API task authorization or successful dispatch.",
            "A restricted MCP process must not be elevated to fix the host OS.",
            "Normal authorized host SSH permissions and MCP sandbox permissions differ.",
        ],
    }


def get_units(runner: Callable[..., Any] = subprocess.run) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for unit in UNITS:
        argv = [
            "systemctl",
            "--user",
            "show",
            unit,
            "--no-pager",
            *["-p" + key for key in PROPERTIES],
        ]
        try:
            p = runner(argv, capture_output=True, text=True, timeout=5, check=False)
            result[unit] = parse_unit_properties(p.stdout) if p.returncode == 0 else {}
        except (OSError, subprocess.TimeoutExpired):
            result[unit] = {}
    return result


def get_session_no_new_privs() -> bool:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("NoNewPrivs:"):
                return line.partition(":")[2].strip() == "1"
    except OSError:
        pass
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = evaluate(get_units(), get_session_no_new_privs())
    print(json.dumps(report, sort_keys=True))
    return 2 if args.check and not report["mcp_and_fleet_exec_service_checks_pass"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
