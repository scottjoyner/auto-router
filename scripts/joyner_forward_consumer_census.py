#!/usr/bin/env python3
"""Bounded, privacy-preserving connection census for Joyner's legacy forwards.

Reads only kernel TCP socket metadata with ss. Never captures payloads, model
prompts, tokens, secrets or destination process memory. Never changes sockets.
Empty samples do not prove a lack of actual clients.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import ipaddress
import json
import os
import socket
import subprocess
import time

LAN = "192.168.1.79"
TAILNET = "100.83.215.83"
LEGACY_PORTS = (1234, 1236)
ROUTES = {
    (LAN, 1234): "minicpm_lan",
    (TAILNET, 1234): "minicpm_tailnet",
    (LAN, 1236): "ling_lan",
    (TAILNET, 1236): "ling_tailnet",
}


def address(value: str) -> tuple[str, int] | None:
    # Literal IPv4 only. No hostname resolution, IPv6 ambiguity, or spoofable
    # X-Forwarded-For headers.
    host, separator, port = value.rpartition(":")
    if not separator or not port.isdecimal():
        return None
    try:
        parsed = ipaddress.ip_address(host)
        number = int(port)
    except ValueError:
        return None
    if not isinstance(parsed, ipaddress.IPv4Address) or not 0 < number <= 65535:
        return None
    return str(parsed), number


def parse_ss(output: str) -> set[tuple[str, str]]:
    """Return only route-label and literal peer-IP pairs from TCP ESTAB lines."""
    observed: set[tuple[str, str]] = set()
    for line in output.splitlines():
        parts = line.split()
        # ss -H -tn state established:
        # ESTAB Recv-Q Send-Q Local-Address:Port Peer-Address:Port
        if len(parts) < 5 or parts[0] != "ESTAB":
            continue
        local, peer = address(parts[3]), address(parts[4])
        if not local or not peer or peer[0] in ("0.0.0.0", "127.0.0.1"):
            continue
        route = ROUTES.get(local)
        if route:
            observed.add((route, peer[0]))
    return observed


def read_ss() -> str:
    result = subprocess.run(
        ["ss", "-H", "-tn", "state", "established"],
        capture_output=True, text=True, check=False, timeout=3,
    )
    if result.returncode:
        raise OSError("tcp_socket_read_failed")
    return result.stdout


def census(*, duration: float, interval: float,
           hostname: str | None = None, reader=read_ss,
           clock=time.monotonic, sleeper=time.sleep) -> dict:
    current = (hostname or socket.gethostname()).split(".")[0].lower()
    if not 0 < duration <= 60 or not 0.1 <= interval <= 5:
        raise ValueError("duration or interval outside bounded range")
    if current != "joyner":
        return {
            "status": "OBSERVER_NOT_JOYNER", "read_only": True,
            "samples": 0, "unique_clients_observed": None,
            "client_inventory_complete": False, "may_disable_forward": False,
            "may_promote_router": False, "raw_peer_addresses_emitted": False,
        }
    # Per-invocation salt: report cannot link client identities across runs,
    # and never prints exact IPs or model/prompt data.
    salt = os.urandom(32)
    routes = {name: {"samples_with_connections": 0, "connection_observations": 0,
                     "client_tags": set()} for name in ROUTES.values()}
    failures = 0
    samples = 0
    deadline = clock() + duration
    # Limit transient system calls to no more than 1 + ceil(duration/interval).
    while True:
        try:
            socket_snapshot = reader()
            pairs = parse_ss(socket_snapshot)
            for name, addr in pairs:
                node = routes[name]
                node["client_tags"].add(hashlib.blake2b(
                    addr.encode("ascii"), digest_size=12, key=salt).hexdigest())
                node["connection_observations"] += 1
            for name in {name for name, _ in pairs}:
                routes[name]["samples_with_connections"] += 1
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            failures += 1
        samples += 1
        remaining = deadline - clock()
        if remaining <= 0:
            break
        sleeper(min(interval, remaining))
    tagged: set[str] = set()
    output_routes: dict[str, dict] = {}
    for name, value in routes.items():
        tagged.update(value["client_tags"])
        output_routes[name] = {
            "samples_with_connections": value["samples_with_connections"],
            "connection_observations": value["connection_observations"],
            "unique_client_ips_seen": len(value["client_tags"]),
        }
    return {
        "status": "OBSERVATIONS_INCOMPLETE" if failures else
                  "BOUNDED_SAMPLE_COMPLETE_NOT_CUSTODY_PROOF",
        "read_only": True, "duration_requested_seconds": duration,
        "samples": samples, "socket_read_errors": failures,
        "unique_clients_observed": len(tagged),
        "client_inventory_complete": False,
        "raw_peer_addresses_emitted": False,
        "model_requests_sent": False,
        "may_disable_forward": False,
        "may_promote_router": False,
        "may_train": False,
        "routes": output_routes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=12.0)
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()
    try:
        report = census(duration=args.duration, interval=args.interval)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, sort_keys=True))
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
