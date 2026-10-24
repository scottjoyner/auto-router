#!/usr/bin/env python3
"""Read-only Joyner inference listener/auth-boundary diagnostic, not routing authority.

Only GET /v1/models is used; never sends model prompts, bearer tokens, POSTs,
or writes to running model servers. This report cannot admit a provider.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


@dataclass(frozen=True)
class Endpoint:
    name: str
    host: str
    port: int
    scope: str


def endpoints(lan_ip: str, tailnet_ip: str) -> tuple[Endpoint, ...]:
    return (
        Endpoint("toolcall-lan", lan_ip, 1234, "LAN"),
        Endpoint("ling-lan", lan_ip, 1236, "LAN"),
        Endpoint("toolcall-tailnet-forward", tailnet_ip, 1234, "Tailnet"),
        Endpoint("ling-tailnet-forward", tailnet_ip, 1236, "Tailnet"),
        Endpoint("toolcall-tailnet-guarded", tailnet_ip, 18321, "Tailnet"),
        Endpoint("minicpm-tailnet-guarded", tailnet_ip, 18320, "Tailnet"),
    )


def _valid_ip(value: str) -> bool:
    import ipaddress
    try:
        return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
    except ValueError:
        return False


def listener_addresses(ss_text: str) -> set[tuple[str, int]]:
    """Parse only bound IPv4 TCP listening local endpoints from ss -lnt."""
    matches: set[tuple[str, int]] = set()
    for line in ss_text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "LISTEN":
            continue
        local = parts[3]
        host, sep, port = local.rpartition(":")
        if sep and _valid_ip(host) and port.isascii() and port.isdigit():
            matches.add((host, int(port)))
    return matches


def read_listeners() -> tuple[set[tuple[str, int]], str | None]:
    try:
        done = subprocess.run(
            ["ss", "-H", "-lnt"], capture_output=True,
            check=False, timeout=4, text=True,
        )
        if done.returncode:
            return set(), "ss_failed"
        return listener_addresses(done.stdout), None
    except (OSError, subprocess.TimeoutExpired):
        return set(), "ss_unavailable"


def read_models_status(endpoint: Endpoint, timeout: float) -> tuple[int | None, str | None]:
    # Fixed provider endpoints only; no caller-supplied URI and no proxy-env leak.
    url = f"http://{endpoint.host}:{endpoint.port}/v1/models"
    opener = build_opener(ProxyHandler({}))
    request = Request(url, method="GET", headers={
        "User-Agent": "joyner-forward-auth-audit/1.0",
        "Accept": "application/json",
    })
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, None
    except HTTPError as exc:
        return exc.code, None
    except (URLError, TimeoutError, OSError) as exc:
        return None, type(exc).__name__


def audit(lan_ip: str, tailnet_ip: str, *,
          probe=read_models_status, timeout: float = 2.5,
          observed_listeners: set[tuple[str, int]] | None = None,
          node_hostname: str | None = None) -> dict:
    if not _valid_ip(lan_ip) or not _valid_ip(tailnet_ip):
        raise ValueError("Both endpoint addresses must be valid IPv4 literals")
    host = (node_hostname if node_hostname is not None else socket.gethostname()).lower().split(".")[0]
    correct_host = host == "joyner"
    read_error = None
    if observed_listeners is None:
        observed_listeners, read_error = read_listeners()
    rows: list[dict] = []
    for endpoint in endpoints(lan_ip, tailnet_ip):
        locally_bound = (endpoint.host, endpoint.port) in observed_listeners if correct_host else None
        # Only probe interfaces from their own host to avoid assuming network reachability.
        status, error = probe(endpoint, timeout) if correct_host else (None, "wrong_observer_host")
        if status == 200:
            classification = "CATALOG_READABLE_WITHOUT_CREDENTIAL"
        elif status in (401, 403):
            classification = "MISSING_CREDENTIAL_REJECTED"
        elif status is None:
            classification = "UNVERIFIED"
        else:
            classification = "UNEXPECTED_HTTP_RESPONSE"
        rows.append({
            "name": endpoint.name,
            "scope": endpoint.scope,
            "host": endpoint.host,
            "port": endpoint.port,
            "listener_bound_on_joyner": locally_bound,
            "models_no_credential_http": status,
            "classification": classification,
            "error": error,
            "cross_host_reachability_verified": False,
            "approved_for_dispatch": False,
        })
    exposed = [r["name"] for r in rows
               if r["classification"] == "CATALOG_READABLE_WITHOUT_CREDENTIAL"
               and r["listener_bound_on_joyner"] is True]
    guarded = [r["name"] for r in rows
               if r["classification"] == "MISSING_CREDENTIAL_REJECTED"]
    return {
        "status": "PERIMETER_NO_GO",
        "read_only": True,
        "credential_sent": False,
        "model_generation_performed": False,
        "observer_host_matches": correct_host,
        "listener_enumeration_error": read_error,
        "unauthenticated_model_catalogs": exposed,
        "missing_credential_rejected": guarded,
        "approved_for_routing": False,
        "approved_for_training": False,
        "external_reachability_tested": False,
        "endpoints": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lan-ip", default="192.168.1.79")
    ap.add_argument("--tailnet-ip", default="100.83.215.83")
    ap.add_argument("--timeout", type=float, default=2.5)
    args = ap.parse_args()
    if not 0 < args.timeout <= 8:
        ap.error("timeout must be greater than 0 and <=8 seconds")
    try:
        output = audit(args.lan_ip, args.tailnet_ip, timeout=args.timeout)
    except ValueError as exc:
        ap.error(str(exc))
    print(json.dumps(output, sort_keys=True))
    # An audit with no independently proven client authorization is never a GO.
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
