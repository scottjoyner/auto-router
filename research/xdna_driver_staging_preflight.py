#!/usr/bin/env python3
"""Read-only, fail-closed AMD XDNA driver staging readiness report.

This reports evidence only. It cannot authorize deployment, install modules,
sign artifacts, alter Secure Boot, or access the NPU for inference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

VENDOR_GEM_SHA256 = "cc2a1ac4fff072b751cfb26f48d9df355cff452488807e9c8a630a102d759b84"
VENDOR_FIX = "vm_flags_mod(vma, VM_MIXEDMAP, VM_PFNMAP);"
VENDOR_PATH = Path(
    "/usr/src/xrt-amdxdna-2.25.260102.56.release/drivers/accel/amdxdna/amdxdna_gem.c"
)
DEVICE = "/dev/accel/accel0"
CRITICAL_OS_PACKAGES = (
    "libc6",
    "libc-bin",
    "locales",
    "grub-efi-amd64-signed",
    "shim-signed",
    "libamdhip64-7",
)


def run_command(*args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=4, check=False)
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def kernel_images() -> list[str]:
    # A kernel filename without a matching initrd is not a recovery image.
    return sorted(
        p.name
        for p in Path("/boot").glob("vmlinuz-*")
        if p.is_file() and (p.parent / p.name.replace("vmlinuz-", "initrd.img-", 1)).is_file()
    )


def npu_device_is_character() -> bool:
    try:
        return stat.S_ISCHR(os.stat(DEVICE).st_mode)
    except OSError:
        return False


def enrolled_mok_summary(raw: str) -> dict[str, Any]:
    # This is public certificate metadata only: no key material is opened.
    subjects = [
        line.split(":", 1)[1].strip()
        for line in raw.splitlines()
        if line.strip().startswith("Subject:")
    ]
    canonical = sum("Canonical Ltd." in subject for subject in subjects)
    return {
        "observed_enrolled_subject_count": len(subjects),
        "observed_noncanonical_subject_count": len(subjects) - canonical,
        # A MOK list cannot prove a candidate's signing certificate is trusted.
        "candidate_trust_proven": False,
    }


def available_memory_kib() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def critical_os_package_state() -> tuple[bool, list[str]]:
    # dpkg-query is read-only and works without root; do not run dpkg repair.
    rc, raw = run_command(
        "dpkg-query",
        "-W",
        "-f=${Package}\t${db:Status-Abbrev}\n",
        *CRITICAL_OS_PACKAGES,
    )
    statuses: dict[str, str] = {}
    for line in raw.splitlines():
        if "\t" not in line:
            continue
        name, value = line.split("\t", 1)
        statuses[name] = value.strip()
    bad = sorted(name for name in CRITICAL_OS_PACKAGES if statuses.get(name, "") != "ii")
    return rc == 0 and not bad, bad


def collect() -> dict[str, Any]:
    kernel = os.uname().release
    module_rc, active_module = run_command("modinfo", "-F", "filename", "amdxdna")
    _, signer = run_command("modinfo", "-F", "signer", "amdxdna")
    secure_rc, secure = run_command("mokutil", "--sb-state")
    holder_rc, _holders = run_command("fuser", DEVICE)
    mok_rc, mok_enrollment = run_command("mokutil", "--list-enrolled")
    mok_summary = enrolled_mok_summary(mok_enrollment if mok_rc == 0 else "")
    critical_os_ok, critical_os_bad = critical_os_package_state()
    vendor_sha = ""
    fix_count = 0
    try:
        raw = VENDOR_PATH.read_bytes()
        vendor_sha = hashlib.sha256(raw).hexdigest()
        fix_count = raw.count(VENDOR_FIX.encode())
    except OSError:
        pass
    try:
        disk_free = shutil.disk_usage("/").free
        load1 = os.getloadavg()[0]
    except (OSError, ValueError):
        disk_free, load1 = 0, -1.0
    return {
        "hostname": os.uname().nodename,
        "critical_os_packages_configured": critical_os_ok,
        "critical_os_problem_packages": critical_os_bad,
        "kernel": kernel,
        "module_readable": module_rc == 0 and bool(active_module) and Path(active_module).is_file(),
        "active_module": active_module,
        "module_signer_present": bool(signer),
        "secure_boot_known": secure_rc == 0
        and ("enabled" in secure.lower() or "disabled" in secure.lower()),
        "secure_boot_enabled": secure_rc == 0 and "enabled" in secure.lower(),
        "vendor_source_sha256": vendor_sha,
        "vendor_mapping_fix_count": fix_count,
        "dkms_command_installed": shutil.which("dkms") is not None,
        "module_sign_file_available": os.access(
            f"/lib/modules/{kernel}/build/scripts/sign-file", os.X_OK
        ),
        "candidate_signed_module_verified": False,
        "mok_enrollment_metadata_readable": mok_rc == 0,
        "mok_enrollment_subject_count": mok_summary["observed_enrolled_subject_count"],
        "mok_noncanonical_subject_count": mok_summary["observed_noncanonical_subject_count"],
        "mok_trust_verified_for_candidate": False,
        "rollback_boot_proven": False,
        "alternate_kernel_images": kernel_images(),
        "npu_device_present": npu_device_is_character(),
        # fuser exit=1 also occurs when the device is absent. Do not confuse
        # that failure mode with a genuinely idle accelerator.
        "npu_idle_confirmed": npu_device_is_character() and holder_rc == 1,
        "available_memory_kib": available_memory_kib(),
        "load_average_1m": load1,
        "disk_free_bytes": disk_free,
    }


def evaluate(s: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if not s["critical_os_packages_configured"]:
        reasons.append("critical_os_package_state_incomplete")
    if not s["module_readable"]:
        reasons.append("active_module_unknown")
    if not s["secure_boot_known"]:
        reasons.append("secure_boot_state_unknown")
    if s["secure_boot_enabled"] and not s["module_signer_present"]:
        reasons.append("current_module_signature_not_visible")
    if s["vendor_source_sha256"] != VENDOR_GEM_SHA256 or s["vendor_mapping_fix_count"] != 2:
        reasons.append("vendor_source_identity_unverified")
    if not s["module_sign_file_available"]:
        reasons.append("module_signing_tool_unavailable")
    if not s["dkms_command_installed"]:
        reasons.append("dkms_tool_unavailable")
    if not s["candidate_signed_module_verified"]:
        reasons.append("candidate_signature_unverified")
    if not s["mok_trust_verified_for_candidate"]:
        reasons.append("candidate_secure_boot_trust_unverified")
    if not s["rollback_boot_proven"]:
        reasons.append("rollback_boot_unproven")
    if len(s["alternate_kernel_images"]) < 2:
        reasons.append("alternate_kernel_image_missing")
    if not s["npu_device_present"]:
        reasons.append("npu_device_missing_or_not_character")
    if not s["npu_idle_confirmed"]:
        reasons.append("npu_not_verified_idle")
    if not s["mok_enrollment_metadata_readable"]:
        reasons.append("mok_enrollment_inventory_unavailable")
    if s["available_memory_kib"] < 28_000_000:
        reasons.append("host_memory_headroom_insufficient")
    if s["load_average_1m"] > 5 or s["load_average_1m"] < 0:
        reasons.append("host_load_outside_research_gate")
    if s["disk_free_bytes"] < 8 * 1024**3:
        reasons.append("disk_headroom_insufficient")
    if not s["active_module"].endswith("/kernel/drivers/accel/amdxdna/amdxdna.ko.zst"):
        reasons.append("baseline_in_tree_module_changed")
    # This preflight is diagnostic only, never a staging/production permit.
    return {
        "scope": "read_only_research_staging_review",
        "production_route_eligible": False,
        "driver_replacement_authorized": False,
        "isolated_reboot_authorized": False,
        "host": s["hostname"],
        "critical_os_problem_packages": s["critical_os_problem_packages"],
        "kernel": s["kernel"],
        "source_mapping_fix_present": "vendor_source_identity_unverified" not in reasons,
        "signed_candidate_runtime_tested": False,
        "mok_enrollment_subject_count": s["mok_enrollment_subject_count"],
        "mok_noncanonical_subject_count": s["mok_noncanonical_subject_count"],
        "blocked": True,
        "hold_reasons": sorted(set(reasons)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="return nonzero when any gate is held")
    args = parser.parse_args()
    result = evaluate(collect())
    print(json.dumps(result, sort_keys=True))
    return 2 if args.check and result["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
