#!/usr/bin/env python3
"""Read-only GPU owner isolation witness: network listeners and render mode.

This does NOT attest firewall, ACL, process identity, abort, durable fencing or
user ownership. Never treat a passing local snapshot as dispatch admission.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import stat
import subprocess


def inspect_listeners(ss_output: str, required_ports: list[int]) -> list[dict]:
    listeners: dict[int, list[str]] = {port: [] for port in required_ports}
    for line in ss_output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "LISTEN":
            continue
        local_address = parts[3]
        try:
            port = int(local_address.rsplit(":", 1)[-1])
        except ValueError:
            continue
        if port in listeners:
            listeners[port].append(local_address)
    rows = []
    for port in required_ports:
        binds = listeners[port]
        safe = True
        for addr in binds:
            hostname = addr.rsplit(":", 1)[0].strip("[]")
            try:
                # 127.0.0.0/8 and ::1 are local. 100.64.0.0/10, public and
                # wildcard listeners explicitly fail this snapshot check.
                safe = safe and ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                safe = False
        rows.append({
            "port": port,
            "bindings": binds,
            "listening": bool(binds),
            "loopback_only": bool(binds) and safe,
        })
    return rows


def inspect_snapshot(
    ss_output: str,
    ports: list[int],
    render_device: str,
) -> dict:
    entries = inspect_listeners(ss_output, ports)
    try:
        mode = stat.S_IMODE(os.stat(render_device).st_mode)
        group_or_other_rw = bool(mode & 0o007)
        render = {
            "path": render_device,
            "octal_mode": oct(mode),
            "world_access_bits_present": group_or_other_rw,
            "mode_requires_operator_review": group_or_other_rw,
        }
    except OSError:
        render = {
            "path": render_device,
            "octal_mode": None,
            "world_access_bits_present": None,
            "mode_requires_operator_review": True,
        }
    observable_checks_pass = (
        all(item["loopback_only"] for item in entries)
        and render["mode_requires_operator_review"] is False
    )
    return {
        "read_only": True,
        "listeners": entries,
        "render_device": render,
        "observable_preflight_passed": observable_checks_pass,
        "acl_and_firewall_verified": False,
        "durable_fencing_verified": False,
        "worker_abort_verified": False,
        "production_dispatch_admitted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ports", required=True, help="e.g. 18320,18321")
    parser.add_argument("--render-device", default="/dev/dri/renderD128")
    parser.add_argument("--require-observable-isolation", action="store_true")
    options = parser.parse_args()
    try:
        ports = [int(item) for item in options.ports.split(",")]
    except ValueError:
        parser.error("invalid port numbers")
    if not ports or any(not 1 <= port <= 65535 for port in ports) or len(set(ports)) != len(ports):
        parser.error("ports must be unique and in 1–65535")
    try:
        result = subprocess.run(
            ["ss", "-lntH"], capture_output=True, text=True,
            timeout=4, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        print(json.dumps({
            "read_only": True, "observable_preflight_passed": False,
            "production_dispatch_admitted": False, "error": "listener_inspection_failed",
        }))
        return 3
    report = inspect_snapshot(result.stdout, ports, options.render_device)
    print(json.dumps(report, indent=2))
    if options.require_observable_isolation and not report["observable_preflight_passed"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
