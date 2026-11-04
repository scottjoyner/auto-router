#!/usr/bin/env python3
"""Offline, credential-free Fleet Commander standby provisioning preflight.

Does not read or copy private tokens/keys, launch tunnels, bind ports,
edit services, or claim takeover. A standby needs independently approved
identity and control-plane registration to become production eligible.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

CANDIDATE_HOSTS = frozenset({"xwing", "destroyer", "joyner", "deathstar-XPS-8920"})
X1_HOST = "x1-370"
RELAY_PORT = 8971
RELAY_ADDRESS = "100.64.43.123"
EVIDENCE_PATHS = {
    "mcp_tunnel_unit_installed": ".config/systemd/user/openai-mcp-desktop-commander.service",
    "tunnel_client_installed": ".local/bin/tunnel-client",
    "desktop_commander_installed": (
        ".local/share/mcp/desktop-commander/node_modules/.bin/desktop-commander"
    ),
}


def tcp_check(
    host: str,
    port: int,
    *,
    connect: Callable[[tuple[str, int], float], Any] = socket.create_connection,
) -> bool:
    if host != RELAY_ADDRESS or port != RELAY_PORT:
        return False
    try:
        s = connect((host, port), 2.5)
        s.close()
        return True
    except OSError:
        return False


def inspect_host(
    hostname: str, home: Path, tool_lookup: Callable[[str], str | None] = shutil.which
) -> dict[str, Any]:
    findings: dict[str, Any] = {
        "hostname": hostname,
        "candidate_known": hostname in CANDIDATE_HOSTS,
        "node_installed": bool(tool_lookup("node")),
        "npm_installed": bool(tool_lookup("npm")),
        "ssh_installed": bool(tool_lookup("ssh")),
        "tailscale_installed": bool(tool_lookup("tailscale")),
        "kvm_usable": os.access("/dev/kvm", os.R_OK | os.W_OK),
    }
    for label, suffix in EVIDENCE_PATHS.items():
        findings[label] = (home / suffix).is_file()
    return findings


def assess(findings: dict[str, Any], relay_reachable: bool) -> dict[str, Any]:
    gaps: list[str] = []
    if not findings.get("candidate_known"):
        gaps.append("candidate_not_allowlisted")
    if not findings.get("ssh_installed"):
        gaps.append("ssh_client_missing")
    if not findings.get("tailscale_installed"):
        gaps.append("tailnet_client_missing")
    if not findings.get("node_installed"):
        gaps.append("nodejs_missing")
    if not findings.get("npm_installed"):
        gaps.append("npm_missing")
    if not findings.get("tunnel_client_installed"):
        gaps.append("mcp_tunnel_client_not_provisioned")
    if not findings.get("desktop_commander_installed"):
        gaps.append("mcp_server_not_provisioned")
    if not findings.get("mcp_tunnel_unit_installed"):
        gaps.append("dedicated_standby_systemd_unit_not_provisioned")
    if not relay_reachable:
        gaps.append("existing_relay_transport_unreachable")
    # No code path checks or transmits a secret, and no automatically
    # provisioned identity can be inferred from an installed executable.
    gaps.extend(
        (
            "independent_standby_identity_not_verified",
            "separate_control_plane_registration_not_verified",
            "fleet_failover_cutover_not_owner_approved",
            "quorum_or_single_leader_conflict_not_tested",
        )
    )
    return {
        "scope": "credential_free_fleet_commander_standby_preflight",
        "host": str(findings.get("hostname", "")),
        "standby_eligible_for_cutover": False,
        "may_start_tunnel": False,
        "may_copy_credentials": False,
        "may_modify_systemd": False,
        "may_change_control_plane_routing": False,
        "software_prerequisites": {
            k: v for k, v in sorted(findings.items()) if k not in {"hostname", "candidate_known"}
        },
        "existing_x1_relay_transport_reachable": relay_reachable,
        "hold_reasons": sorted(gaps),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="return 2 if standby is not approved")
    args = parser.parse_args()
    hostname = subprocess.run(
        ["hostname"], capture_output=True, text=True, check=False, timeout=3
    ).stdout.strip()
    data = inspect_host(hostname, Path.home())
    result = assess(data, tcp_check(RELAY_ADDRESS, RELAY_PORT))
    print(json.dumps(result, sort_keys=True))
    return 2 if args.check and not result["standby_eligible_for_cutover"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
