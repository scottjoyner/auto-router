#!/usr/bin/env python3
"""Read-only, fail-closed preflight for x1-370 offline root+EFI recovery imaging.

No disk images are made. No mounts, service changes, password reads, or writes.
A PASS of mechanical checks is NOT permission to image or repair a computer.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

ROOT_SIZE = 958_677_385_216
EFI_SIZE = 1_073_741_824
MIN_TARGET_FREE = 1_300_000_000_000  # conservative raw-image+metadata margin


def command(*argv: str) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=6, check=False)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def device_info(path: str) -> dict[str, Any]:
    result: dict[str, Any] = {"path": path, "is_block": False, "mounted": True}
    try:
        info = os.stat(path)
        if not stat.S_ISBLK(info.st_mode):
            return result
        result["is_block"] = True
        identifier = f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}"
        result["device_id"] = identifier
        # Mountinfo field 3 is the filesystem device, even for bind mounts.
        mounts = Path("/proc/self/mountinfo").read_text().splitlines()
        result["mounted"] = any(
            len(parts := line.split(" ")) > 2 and parts[2] == identifier for line in mounts
        )
    except (OSError, ValueError):
        return result
    rc, output = command("lsblk", "-bnro", "SIZE,FSTYPE,PARTUUID", path)
    if rc == 0 and output:
        words = output.splitlines()[0].split()
        if len(words) == 3:
            try:
                result["size"] = int(words[0])
                result["fstype"] = words[1]
                result["partuuid"] = words[2]
            except ValueError:
                pass
    return result


def target_info(path: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": path,
        "exists": False,
        "free_bytes": 0,
        "remote_mount_confirmed": False,
    }
    p = Path(path)
    try:
        result["exists"] = p.is_dir()
        if result["exists"]:
            rc, mount_type = command("findmnt", "-T", str(p), "-no", "FSTYPE")
            # Active SMB mounts and automount placeholders can both be listed.
            # An autofs placeholder alone must never count as off-host storage.
            result["remote_mount_confirmed"] = rc == 0 and any(
                item.strip() in {"cifs", "nfs", "nfs4"} for item in mount_type.splitlines()
            )
            rc, output = command("df", "-B1", "--output=avail", str(p))
            if rc == 0:
                result["free_bytes"] = int(output.splitlines()[-1].strip())
    except (OSError, ValueError, IndexError):
        pass
    return result


def evaluate(
    root: dict[str, Any],
    efi: dict[str, Any],
    target: dict[str, Any],
    euid: int,
    expected_root_partuuid: str = "",
    expected_efi_partuuid: str = "",
) -> dict[str, Any]:
    reasons = []
    for name, dev, size, fstype, expected_id in [
        ("root", root, ROOT_SIZE, "ext4", expected_root_partuuid),
        ("efi", efi, EFI_SIZE, "vfat", expected_efi_partuuid),
    ]:
        if not dev.get("is_block", False):
            reasons.append(name + "_not_block_device")
        if dev.get("mounted", True):
            reasons.append(name + "_still_mounted")
        if dev.get("size") != size or dev.get("fstype") != fstype:
            reasons.append(name + "_unexpected_size_or_filesystem")
        if not expected_id or dev.get("partuuid") != expected_id:
            reasons.append(name + "_partuuid_not_independently_matched")
    if root.get("path") == efi.get("path") or (
        root.get("device_id") is not None and root.get("device_id") == efi.get("device_id")
    ):
        reasons.append("source_partitions_are_identical")
    if not target.get("exists", False) or target.get("free_bytes", 0) < MIN_TARGET_FREE:
        reasons.append("target_absent_or_insufficient_capacity")
    if not target.get("remote_mount_confirmed", False):
        reasons.append("backup_target_not_verified_network_mount")
    if euid != 0:
        reasons.append("not_authorized_rescue_privilege")
    # Console, backup retention, restore and user approval cannot be
    # attested from this program; always refuse autonomous imaging.
    reasons.extend(
        [
            "physical_rescue_console_not_verified",
            "independent_restore_not_verified",
            "owner_imaging_approval_required",
        ]
    )
    return {
        "scope": "offline_imaging_read_only_preflight",
        "blocked": True,
        "may_execute_imaging": False,
        "may_modify_package_database": False,
        "production_driver_admission": False,
        "hold_reasons": sorted(set(reasons)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-device", default="/dev/nvme0n1p6")
    parser.add_argument("--efi-device", default="/dev/nvme0n1p1")
    parser.add_argument("--nas-path", default="/nas/backups")
    parser.add_argument("--expected-root-partuuid", default="")
    parser.add_argument("--expected-efi-partuuid", default="")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = evaluate(
        device_info(args.root_device),
        device_info(args.efi_device),
        target_info(args.nas_path),
        os.geteuid(),
        args.expected_root_partuuid,
        args.expected_efi_partuuid,
    )
    print(json.dumps(report, sort_keys=True))
    return 2 if args.check and report["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
