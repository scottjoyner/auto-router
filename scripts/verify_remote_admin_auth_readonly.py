#!/usr/bin/env python3
"""Read-only anonymous admin-auth acceptance from a REAL LAN/Tailnet client.

Run separately on Destroyer and Lenovo after a supervised router cutover.
No credentials, response bodies, POSTs, redirects, health probes or writes.
The target must be a literal, non-loopback, private/Tailnet IPv4 address.
"""

from __future__ import annotations
import argparse
import ipaddress
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROUTES = ("/admin/providers", "/admin/providers/effective", "/admin/providers/health", "/admin/context", "/admin/circuits", "/admin/agent-workers")
TAILNET = ipaddress.ip_network("100.64.0.0/10")
PRIVATE = tuple(ipaddress.ip_network(x) for x in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def validate_target(address: str) -> str:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        raise ValueError("literal IPv4 address required") from None
    if not isinstance(ip, ipaddress.IPv4Address) or not (
        ip in TAILNET or any(ip in net for net in PRIVATE)
    ):
        raise ValueError("private/Tailnet literal IPv4 address required")
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast:
        raise ValueError("remote target required")
    return str(ip)


def check(target: str, opener) -> dict[str, object]:
    ip = validate_target(target)
    observed = {}
    for path in ROUTES:
        req = Request(f"http://{ip}:8088{path}", method="GET")
        try:
            with opener.open(req, timeout=4) as response:
                status = response.status
                # Intentionally do not call response.read()
        except HTTPError as e:
            status = e.code
        except (URLError, OSError, TimeoutError):
            status = None
        observed[path] = status
    return {
        "schema_version": "router.remote-anonymous-admin-negative-v1",
        "decision": "PASS_REMOTE_401" if all(v == 401 for v in observed.values()) else "HOLD_REMOTE_ADMIN_AUTH",
        "http_status_only": observed,
        "transport": "direct_private_or_tailnet_no_proxy_no_redirect",
        "credentials_sent": False,
        "response_bodies_read": False,
        "writes_performed": False,
        "provider_residency_verified": False,
        "deployment_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-ip", required=True)
    args = parser.parse_args()
    try:
        report = check(args.target_ip, build_opener(ProxyHandler({}), NoRedirect()))
    except ValueError:
        report = {"schema_version": "router.remote-anonymous-admin-negative-v1",
                  "decision": "HOLD_TARGET_INVALID", "credentials_sent": False,
                  "response_bodies_read": False, "deployment_authorized": False}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["decision"] == "PASS_REMOTE_401" else 1


if __name__ == "__main__":
    sys.exit(main())
