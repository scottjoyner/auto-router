#!/usr/bin/env python3
"""Read-only R9700 PCI condition witness for a separately approved systemd unit.

Exit 0 means only that one AMD 1002:7551 PCI device is enumerated.
Exit 1 means absence, ambiguous enumeration, or unreadable evidence.
No driver, ROCm, model, power, restart, network, or credential operation.
"""
import json
import os
from pathlib import Path
import re

PCI_DEVICES = Path("/sys/bus/pci/devices")
PCI_NAME = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$")
MAX_ENTRIES = 512
EXPECTED_VENDOR = "0x1002"
EXPECTED_DEVICE = "0x7551"


def assess(root=PCI_DEVICES, limit=MAX_ENTRIES):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_ENTRIES:
        return "HOLD_INVALID_LIMIT"
    count = 0
    seen = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                seen += 1
                if seen > limit:
                    return "HOLD_PCI_ENUMERATION_TOO_LARGE"
                if not PCI_NAME.fullmatch(entry.name):
                    return "HOLD_UNEXPECTED_PCI_ENTRY"
                base = Path(entry.path)
                vendor = (base / "vendor").read_text(encoding="ascii").strip().lower()
                device = (base / "device").read_text(encoding="ascii").strip().lower()
                if not re.fullmatch(r"0x[0-9a-f]{4}", vendor):
                    return "HOLD_MALFORMED_PCI_ID"
                if not re.fullmatch(r"0x[0-9a-f]{4}", device):
                    return "HOLD_MALFORMED_PCI_ID"
                if vendor == EXPECTED_VENDOR and device == EXPECTED_DEVICE:
                    count += 1
    except (OSError, UnicodeError, ValueError):
        return "HOLD_PCI_EVIDENCE_UNAVAILABLE"
    if count != 1:
        return "HOLD_PCI_R9700_ABSENT" if count == 0 else "HOLD_MULTIPLE_R9700_FUNCTIONS"
    return "PCI_DEVICE_ENUMERATED_ONLY"


def main():
    status = assess()
    print(json.dumps({
        "schema": "joyner-r9700-pci-condition-v1",
        "status": status,
        "hardwareConditionSatisfied": status == "PCI_DEVICE_ENUMERATED_ONLY",
        "driverReady": False,
        "inferenceReady": False,
        "modelLoaded": False,
        "serviceChanged": False,
    }, sort_keys=True))
    # systemd ExecCondition: 1-254 means skip, without an ExecStart failure.
    return 0 if status == "PCI_DEVICE_ENUMERATED_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
