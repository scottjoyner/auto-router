"""Host isolation snapshots are read-only and never grant GPU dispatch."""
from __future__ import annotations

import os
from pathlib import Path

from scripts.gpu_owner_isolation_preflight import (
    inspect_listeners,
    inspect_snapshot,
)

TAILNET = (
    "LISTEN 0 512 100.83.215.83:18320 0.0.0.0:*\n"
    "LISTEN 0 512 100.83.215.83:18321 0.0.0.0:*\n"
)
LOOPBACK = (
    "LISTEN 0 512 127.0.0.1:18320 0.0.0.0:*\n"
    "LISTEN 0 512 [::1]:18321 [::]:*\n"
)


def test_live_joyner_style_tailnet_bindings_are_not_owner_isolated():
    rows = inspect_listeners(TAILNET, [18320, 18321])
    assert all(row["listening"] for row in rows)
    assert all(not row["loopback_only"] for row in rows)


def test_safe_loopback_snapshot_still_never_grants_production_admission(tmp_path: Path):
    render = tmp_path / "renderD128"
    render.write_bytes(b"fixture")
    os.chmod(render, 0o660)
    report = inspect_snapshot(LOOPBACK, [18320, 18321], str(render))
    assert report["observable_preflight_passed"]
    assert report["production_dispatch_admitted"] is False
    assert report["acl_and_firewall_verified"] is False
    assert report["worker_abort_verified"] is False


def test_world_writable_render_device_fails_read_only_preflight(tmp_path: Path):
    render = tmp_path / "renderD128"
    render.write_bytes(b"fixture")
    os.chmod(render, 0o666)
    report = inspect_snapshot(LOOPBACK, [18320, 18321], str(render))
    assert not report["observable_preflight_passed"]
    assert report["render_device"]["world_access_bits_present"]
    assert report["production_dispatch_admitted"] is False


def test_missing_port_and_missing_render_device_fail_closed():
    rows = inspect_listeners(LOOPBACK, [18320, 18321, 18271])
    assert not rows[-1]["listening"]
    report = inspect_snapshot(LOOPBACK, [18320, 18321], "/missing/renderD128")
    assert report["render_device"]["mode_requires_operator_review"]
    assert not report["observable_preflight_passed"]


def test_wildcard_and_public_listener_fails_even_if_loopback_is_present():
    text = (
        "LISTEN 0 512 127.0.0.1:18320 0.0.0.0:*\n"
        "LISTEN 0 512 0.0.0.0:18320 0.0.0.0:*\n"
    )
    rows = inspect_listeners(text, [18320])
    assert len(rows[0]["bindings"]) == 2
    assert not rows[0]["loopback_only"]


def test_ipv6_loopback_is_recognized():
    rows = inspect_listeners("LISTEN 0 512 [::1]:18271 [::]:*\n", [18271])
    assert rows[0]["loopback_only"]
