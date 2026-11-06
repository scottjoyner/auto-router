"""Synthetic-only tests for fail-closed rescue USB admission."""
from __future__ import annotations

from scripts.x1_rescue_usb_readonly import assess


def disk(**changes):
    base = {"path": "/dev/sdz", "type": "disk", "rm": True,
            "ro": False, "tran": "usb", "size": 16_000_000_000,
            "fstype": None, "mountpoints": [None], "children": None}
    base.update(changes)
    return {"blockdevices": [base]}


def test_unselected_is_hold():
    r = assess(disk(), None)
    assert r["gate"] == "HOLD"
    assert not r["approved_for_erasure"]
    assert "NO_EXPLICIT_SELECTED_DISK" in r["reasons"]


def test_claimed_blank_usb_still_requires_physical_approval():
    r = assess(disk(), "/dev/sdz")
    assert r["gate"] == "HOLD"
    assert not r["approved_for_erasure"]
    assert not r["device_write_attempted"]
    assert r["reasons"] == ["PHYSICAL_OWNER_EMPTY_MEDIA_APPROVAL_MISSING"]


def test_existing_partition_even_unmounted_rejected():
    children = [{"path": "/dev/sdz1", "type": "part",
                 "fstype": None, "mountpoints": [None]}]
    r = assess(disk(children=children), "/dev/sdz")
    assert "EXISTING_PARTITIONS_OR_CHILD_DEVICES" in r["reasons"]


def test_mounted_existing_data_rejected():
    r = assess(disk(children=[{"path": "/dev/sdz1", "type": "part",
                               "fstype": "vfat", "mountpoints": ["/media/data"]}]),
               "/dev/sdz")
    assert "EXISTING_PARTITIONS_OR_CHILD_DEVICES" in r["reasons"]


def test_non_usb_internal_system_disk_rejected():
    r = assess(disk(path="/dev/nvme0n1", tran="nvme", rm=False),
               "/dev/nvme0n1")
    assert "NOT_REMOVABLE_USB" in r["reasons"]


def test_read_only_and_small_device_rejected():
    r = assess(disk(ro=True, size=4_000_000_000), "/dev/sdz")
    assert "READONLY_OR_UNKNOWN_DEVICE_STATE" in r["reasons"]
    assert "INSUFFICIENT_OR_UNVERIFIED_CAPACITY" in r["reasons"]


def test_ambiguous_devices_fail_closed():
    d = disk()["blockdevices"][0]
    r = assess({"blockdevices": [d, dict(d)]}, "/dev/sdz")
    assert "UNIQUE_BLOCK_DISK_IDENTITY_NOT_VERIFIED" in r["reasons"]


def test_wrong_path_and_inventory_rejected():
    assert "INVALID_DISK_PATH" in assess(disk(), "../../sda")["reasons"]
    try:
        assess({"blockdevices": None}, "/dev/sdz")
    except ValueError:
        pass
    else:
        raise AssertionError("must reject invalid inventory")
