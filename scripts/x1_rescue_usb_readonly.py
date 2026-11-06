#!/usr/bin/env python3
"""Read-only rescue USB admission. Always HOLD; never writes block devices."""
from __future__ import annotations

import argparse
import json
import re
import subprocess

USB_NAME = re.compile(r"^/dev/[a-zA-Z0-9_-]{1,48}$")
MIN_BYTES = 8_000_000_000


def assess(snapshot: dict, candidate: str | None) -> dict:
    reasons: list[str] = []
    disks = snapshot.get("blockdevices")
    if not isinstance(disks, list) or len(disks) > 256:
        raise ValueError("invalid lsblk device inventory")
    matches = [d for d in disks if isinstance(d, dict)
               and d.get("path") == candidate and d.get("type") == "disk"]
    if not candidate:
        reasons.append("NO_EXPLICIT_SELECTED_DISK")
    elif not USB_NAME.fullmatch(candidate):
        reasons.append("INVALID_DISK_PATH")
    if len(matches) != 1:
        reasons.append("UNIQUE_BLOCK_DISK_IDENTITY_NOT_VERIFIED")
    else:
        disk = matches[0]
        if disk.get("tran") != "usb" or disk.get("rm") is not True:
            reasons.append("NOT_REMOVABLE_USB")
        if disk.get("ro") is not False:
            reasons.append("READONLY_OR_UNKNOWN_DEVICE_STATE")
        size = disk.get("size")
        if type(size) is not int or size < MIN_BYTES:
            reasons.append("INSUFFICIENT_OR_UNVERIFIED_CAPACITY")
        if disk.get("fstype") is not None:
            reasons.append("EXISTING_FILESYSTEM_SIGNATURE")
        mounts = disk.get("mountpoints")
        if not isinstance(mounts, list) or any(x is not None for x in mounts):
            reasons.append("DISK_MOUNTED_OR_UNVERIFIABLE")
        children = disk.get("children")
        if children:
            reasons.append("EXISTING_PARTITIONS_OR_CHILD_DEVICES")
        elif children is not None and not isinstance(children, list):
            reasons.append("UNVERIFIABLE_CHILDREN")
    reasons.append("PHYSICAL_OWNER_EMPTY_MEDIA_APPROVAL_MISSING")
    return {
        "schema": "x1.readonly-rescue-usb-admission.v1",
        "candidate": candidate,
        "reasons": reasons,
        "device_write_attempted": False,
        "block_device_opened": False,
        "approved_for_erasure": False,
        "ready_for_boot": False,
        "gate": "HOLD",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", default=None,
                        help="Disk for read-only assessment only; never a write target")
    args = parser.parse_args()
    command = ["lsblk", "--json", "--bytes", "--output",
               "NAME,PATH,TYPE,RM,RO,TRAN,FSTYPE,MOUNTPOINTS,SIZE"]
    try:
        first = subprocess.run(command, capture_output=True, text=True,
                               timeout=8, check=True)
        second = subprocess.run(command, capture_output=True, text=True,
                                timeout=8, check=True)
        if json.loads(first.stdout) != json.loads(second.stdout):
            raise ValueError("block device inventory changed between reads")
        result = assess(json.loads(first.stdout), args.candidate)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError,
            json.JSONDecodeError):
        result = {"schema": "x1.readonly-rescue-usb-admission.v1",
                  "reasons": ["INVENTORY_UNVERIFIED"], "gate": "HOLD",
                  "approved_for_erasure": False, "device_write_attempted": False}
    print(json.dumps(result, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
