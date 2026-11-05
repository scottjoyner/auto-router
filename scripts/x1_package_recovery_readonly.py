#!/usr/bin/env python3
"""Read-only host package-health inventory. NEVER authorizes package repair/reboot."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable

DPKG_FORMAT = "$" + "{binary:Package}\t$" + "{db:Status-Abbrev}\t$" + "{Version}\\n"
CRITICAL = frozenset({"libc6", "libc-bin", "libc6-dev", "libc-dev-bin", "locales", "grub-efi-amd64", "shim-signed"})


def inventory_packages(output: str) -> dict:
    """Aggregate only; never publish package inventory, paths or command output."""
    pending = 0
    reinst = 0
    critical: dict[str, dict[str, str]] = {}
    seen_packages: set[str] = set()
    native_libc_state: str | None = None
    total = 0
    for line in output.splitlines():
        cols = line.split("\t")
        # dpkg Status-Abbrev is Want/Status/Error. An unknown or future
        # state MUST NOT become an apparent clean host by default.
        if (len(cols) != 3 or not cols[0] or not cols[2]
                or not re.fullmatch(r"[uihrp][ncHUFWti][ R]", cols[1])):
            raise ValueError("unverifiable dpkg status record")
        package, status, version = cols
        if package in seen_packages:
            raise ValueError("ambiguous repeated dpkg package identity")
        seen_packages.add(package)
        # The designated x1-370 host is x86_64. A healthy foreign-architecture
        # libc6:i386 package must never establish native system glibc readiness.
        # dpkg may omit the architecture suffix for native packages.
        if package in {"libc6", "libc6:amd64"}:
            native_libc_state = status
        total += 1
        # A requested install/hold is complete only when installed; a
        # requested removal is complete only when absent or at rc state.
        # A half-installed, unpacked, half-configured, triggers-pending,
        # reinst-required or interrupted requested removal is incomplete.
        complete = status in {"ii ", "hi ", "un ", "rn ", "pn ", "rc "}
        is_pending = not complete
        if is_pending:
            pending += 1
        if status[2] == "R":
            reinst += 1
        base_name = package.split(":", 1)[0]
        if base_name in CRITICAL:
            if not re.fullmatch(r"[A-Za-z0-9.:+~_-]{1,100}", version):
                raise ValueError("unverifiable critical package version")
            previous = critical.get(base_name)
            # Multiarch names collapse to the same critical family. Preserve
            # its worst observed state instead of overwriting broken libc:i386
            # with a later healthy libc:amd64 record (or the reverse).
            if previous is None or (
                is_pending and previous["state"] in {"ii", "hi"}
            ):
                critical[base_name] = {"state": status.strip(), "version": version}
    if total == 0 or "libc6" not in critical:
        raise ValueError("no credible package inventory")
    return {"total_packages": total, "incomplete_installed_packages": pending,
            "reinstallation_required_packages": reinst, "critical_packages": critical,
            "native_amd64_libc_installed": native_libc_state in {"ii ", "hi "}}


def _meminfo(path: Path) -> dict[str, int | None]:
    keys = {"MemAvailable", "SwapTotal", "SwapFree"}
    values: dict[str, int | None] = {k: None for k in keys}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"(MemAvailable|SwapTotal|SwapFree):\s+(\d+)\s+kB", line)
        if match:
            values[match[1]] = int(match[2])
    return values


def _os_info(path: Path) -> dict[str, str]:
    info: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r'(ID|VERSION_ID)=(?:"([^"\n]*)"|([A-Za-z0-9_.+-]+))', line)
        if match:
            info[match[1]] = match[2] if match[2] is not None else match[3]
    return {"id": info.get("ID", "UNVERIFIED"), "version": info.get("VERSION_ID", "UNVERIFIED")}


def _free_bytes(path: Path) -> int | None:
    if not path.exists():
        return None
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def _efi_mount_verified(proc: Path, efi: Path) -> bool:
    """Require exactly one mounted EFI vfat filesystem, not an empty directory.

    Reads only mountpoint/filesystem metadata. Neither device names nor raw
    mount records are emitted into the aggregate report.
    """
    if not efi.is_dir():
        return False
    with (proc / "self" / "mountinfo").open(encoding="utf-8") as source:
        data = source.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        return False
    mounts = []
    for line in data.splitlines():
        fields = line.split()
        if len(fields) < 10 or fields[4] != str(efi):
            continue
        if fields.count("-") != 1:
            return False
        separator = fields.index("-")
        mounts.append(
            separator + 3 < len(fields)
            and fields[3] == "/"
            and fields[separator + 1] == "vfat"
        )
    return len(mounts) == 1 and mounts[0]


def collect(*, run: Callable = subprocess.run, proc: Path = Path("/proc"),
            etc: Path = Path("/etc"), root: Path = Path("/"),
            efi: Path = Path("/boot/efi")) -> dict:
    """Only fixed read commands; never logs raw errors or command arguments."""
    report: dict = {"contract": "x1-package-recovery-readonly-v1", "production_change_permitted": False,
                    "package_repair_permitted": False, "reboot_permitted": False,
                    "root_efi_restore_certified": False, "console_recovery_certified": False,
                    "rollback_rehearsal_certified": False, "decision": "HOLD"}
    try:
        result = run(["dpkg-query", "-W", f"-f={DPKG_FORMAT}"], check=False,
                     capture_output=True, text=True, timeout=12)
        if result.returncode != 0:
            raise ValueError("dpkg-query returned nonzero")
        report["dpkg"] = inventory_packages(result.stdout)
        report["os"] = _os_info(etc / "os-release")
        report["memory_kib"] = _meminfo(proc / "meminfo")
        report["filesystem_available_bytes"] = {"root": _free_bytes(root), "efi": _free_bytes(efi)}
        report["efi_mounted_vfat"] = _efi_mount_verified(proc, efi)
        report["uname_kernel"] = os.uname().release
        try:
            sb = run(["mokutil", "--sb-state"], check=False, capture_output=True,
                     text=True, timeout=8)
            report["secure_boot"] = ("enabled" if sb.returncode == 0 and
                                     "SecureBoot enabled" in sb.stdout else
                                     "disabled" if sb.returncode == 0 and
                                     "SecureBoot disabled" in sb.stdout else "UNVERIFIED")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            report["secure_boot"] = "UNVERIFIED"
        # Clean dpkg counters are not sufficient: a host with only residual
        # libc configuration (rc), missing EFI, or unreadable boot evidence
        # must never produce a successful operator-preflight exit.
        libc_ready = (
            report["dpkg"]["native_amd64_libc_installed"] is True
            and report["dpkg"]["critical_packages"]["libc6"]["state"] in {"ii", "hi"}
        )
        report["package_state"] = (
            "NO_DPKG_INCOMPLETE_INSTALLED_DETECTED"
            if libc_ready and not report["dpkg"]["incomplete_installed_packages"]
            and not report["dpkg"]["reinstallation_required_packages"]
            else "INCOMPLETE"
        )
        report["observations_complete"] = (
            report["os"]["id"] == "ubuntu"
            and report["os"]["version"] != "UNVERIFIED"
            and all(isinstance(n, int) and n >= 0 for n in report["memory_kib"].values())
            and all(isinstance(n, int) and n > 0
                    for n in report["filesystem_available_bytes"].values())
            and report["efi_mounted_vfat"] is True
            and report["secure_boot"] in {"enabled", "disabled"}
        )
        if not report["observations_complete"]:
            report["failure"] = "READ_ONLY_EVIDENCE_INCOMPLETE"
    except (ValueError, FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired, UnicodeError):
        report["observations_complete"] = False
        report["package_state"] = "UNVERIFIED"
        report["failure"] = "READ_ONLY_EVIDENCE_UNAVAILABLE"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only", action="store_true", required=True,
                        help="emit aggregate package-health evidence; never repair")
    args = parser.parse_args(argv)
    if not args.read_only:
        return 2
    report = collect()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if (report["observations_complete"] is True
                 and report["package_state"] == "NO_DPKG_INCOMPLETE_INSTALLED_DETECTED") else 2


if __name__ == "__main__":
    sys.exit(main())
