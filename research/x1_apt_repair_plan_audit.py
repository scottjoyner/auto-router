#!/usr/bin/env python3
"""Read-only audit of a proposed apt fix-broken transaction on x1-370.

Fixed command is apt-get -s (simulation only). This does not authorize
apt/dpkg execution, service restarts, driver replacement, or reboot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

DPKG_STATUS = Path("/var/lib/dpkg/status")
EXPECTED_STATUS_SHA = "232da7591bda5821062ff5b2fe4ae649b177b382b5bb8dae795ddffd9e0795dd"
EXPECTED_PLAN_SHA = "33475a9a474331a27e389c071142a95585a1b82dcc2467a3f39b714cc3263f21"

EXPECTED_INSTALL_NAMES = frozenset(
    {
        "libc6-dbg",
        "libc-dev-bin",
        "make",
        "dpkg-dev",
        "libdpkg-perl",
        "libc6-dev",
        "libc6",
        "libc-gconv-modules-extra",
        "libc-bin",
    }
)
CRITICAL_CONFIGURE = frozenset(
    {"libc6", "libc-bin", "locales", "grub-efi-amd64-signed", "shim-signed", "libamdhip64-7"}
)
GLIBC_TARGET_VERSION = "2.43-2ubuntu2.4"


def parse_simulation(output: str) -> dict[str, Any]:
    changes: dict[str, dict[str, str]] = {"install": {}, "configure": {}, "remove": {}}
    summary: dict[str, int] = {}
    errors: list[str] = []
    for line in output.splitlines():
        match = re.match(r"^([0-9]+) upgraded, ([0-9]+) newly installed, ([0-9]+) to remove", line)
        if match:
            summary = dict(
                zip(("upgraded", "new", "removed"), map(int, match.groups()), strict=True)
            )
        if line.startswith(("E:", "dpkg: error:")):
            errors.append("apt_simulation_error_present")
        m = re.match(r"^(Inst|Conf|Remv)\s+(\S+)(?:\s+(.*))?$", line)
        if not m:
            continue
        kind, name, suffix = m.groups()
        bucket = {"Inst": "install", "Conf": "configure", "Remv": "remove"}[kind]
        suffix = suffix or ""
        version = re.search(r"\(([^\s)]+)", suffix)
        # The first parenthesized version is the target for Inst/Conf.
        changes[bucket][name] = version.group(1) if version else ""
    return {"summary": summary, "changes": changes, "errors": errors}


def evaluate(
    parsed: dict[str, Any],
    status_sha: str,
    apt_exit: int,
    timeout: bool = False,
    expected_plan_sha: str = EXPECTED_PLAN_SHA,
) -> dict[str, Any]:
    reasons: list[str] = []
    summary = parsed["summary"]
    changes = parsed["changes"]
    installs = changes["install"]
    configurations = changes["configure"]
    removals = changes["remove"]
    if status_sha != EXPECTED_STATUS_SHA:
        reasons.append("dpkg_status_drift")
    if apt_exit != 0 or timeout or parsed["errors"]:
        reasons.append("apt_simulation_failed")
    if summary != {"upgraded": 8, "new": 1, "removed": 0}:
        reasons.append("unexpected_transaction_counts")
    if removals or summary.get("removed", 1) != 0:
        reasons.append("unexpected_package_removal")
    if set(installs) != EXPECTED_INSTALL_NAMES:
        reasons.append("unexpected_install_set")
    if any(
        installs.get(name) != GLIBC_TARGET_VERSION
        for name in (
            "libc6",
            "libc-bin",
            "libc6-dbg",
            "libc6-dev",
            "libc-dev-bin",
            "libc-gconv-modules-extra",
        )
    ):
        reasons.append("unexpected_glibc_target_version")
    if not CRITICAL_CONFIGURE.issubset(configurations):
        reasons.append("critical_package_configure_steps_missing")
    # Require an inspectable list and digest without storing raw apt output.
    plan = {
        "summary": summary,
        "install": dict(sorted(installs.items())),
        "configure": dict(sorted(configurations.items())),
        "remove": dict(sorted(removals.items())),
    }
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    if plan_hash != expected_plan_sha:
        reasons.append("unreviewed_full_transaction_drift")
    return {
        "scope": "read_only_apt_fix_broken_simulation",
        "blocked": True,
        "may_execute_repair": False,
        "may_restart_services": False,
        "may_modify_bootloader": False,
        "production_npu_route_eligible": False,
        "baseline_matches": not reasons,
        "observed_dpkg_sha256": status_sha,
        "plan_sha256": plan_hash,
        "summary": summary,
        "candidate_install_packages": sorted(installs),
        "critical_configure_packages": sorted(CRITICAL_CONFIGURE & configurations.keys()),
        "remove_packages": sorted(removals),
        "unexpected_or_unverified_facts": sorted(set(reasons)),
        "physical_hold_reasons": [
            "full_offline_root_efi_image_not_verified",
            "out_of_band_console_not_verified",
            "independent_boot_restore_not_verified",
            "actual_service_restart_behavior_not_rehearsed",
            "owner_maintenance_approval_required",
        ],
    }


def run() -> tuple[int, str, bool]:
    try:
        p = subprocess.run(
            ["apt-get", "-s", "-o", "Debug::NoLocking=1", "-f", "install"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
        return p.returncode, p.stdout + "\n" + p.stderr, False
    except (OSError, subprocess.TimeoutExpired):
        return 127, "", True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 2 while on hold")
    args = parser.parse_args()
    try:
        status_sha = hashlib.sha256(DPKG_STATUS.read_bytes()).hexdigest()
    except OSError:
        status_sha = ""
    rc, output, timed_out = run()
    report = evaluate(parse_simulation(output), status_sha, rc, timed_out)
    print(json.dumps(report, sort_keys=True))
    return 2 if args.check and report["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
