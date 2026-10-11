#!/usr/bin/env python3
"""Read-only Auto-Router admin-auth cutover evidence from one network vantage.

Uses GET only, no redirects/proxies, no admin POST, no inference and no
credential output. Exit 0 means the observed admin boundary meets the narrow
post-PR41 contract; it is NOT a release/admission/physical-node approval.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

_ANONYMOUS_PATHS = (
    "/admin/providers",
    "/admin/circuits",
    "/admin/agent-workers",
    "/admin/runtime-projection",
)
_EFFECTIVE_PATH = "/admin/providers/effective"
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_MAX_CATALOG_BYTES = 512 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def checked_origin(value: str) -> str:
    """Permit only an explicit private network or Tailscale router origin."""
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname
        port = parts.port  # Force validation of malformed ports.
        if (
            parts.scheme not in {"http", "https"}
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
            or port == 0
        ):
            raise ValueError("invalid router origin")
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            safe = host.lower() == "localhost" or host.lower().endswith(".ts.net")
        else:
            safe = addr.is_private or addr.is_loopback or addr in _CGNAT
        if not safe:
            raise ValueError("router origin is not private")
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("invalid private router origin") from exc
    return value.strip().rstrip("/")


def _read(
    origin: str,
    path: str,
    *,
    token: str = "",
    opener=None,
) -> tuple[int, bytes | None]:
    if path not in (*_ANONYMOUS_PATHS, _EFFECTIVE_PATH):
        raise ValueError("endpoint not allowlisted")
    if opener is None:
        # Never send the admin token through an environment-configured proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Admin-Token"] = token
    req = urllib.request.Request(origin + path, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=4.0) as resp:
            status = int(resp.status)
            if token and path == _EFFECTIVE_PATH and status == 200:
                body = resp.read(_MAX_CATALOG_BYTES + 1)
                return (status, body if len(body) <= _MAX_CATALOG_BYTES else None)
            return status, None  # Never read uncredentialed admin response bodies.
    except urllib.error.HTTPError as exc:
        return int(exc.code), None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, None


def _valid_effective_snapshot(body: bytes | None) -> bool:
    if body is None:
        return False
    try:
        data = json.loads(body)
    except (UnicodeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    rows = data.get("providers")
    return bool(
        type(data.get("schema_version")) is int
        and data["schema_version"] == 1
        and data.get("kind") == "EFFECTIVE_ROUTER_MEMORY_READ_ONLY"
        and data.get("state_source") == "LIVE_ASGI_STATE_NOT_BOOTSTRAP_PROVIDER_FILE"
        and data.get("no_probes_or_dispatch") is True
        and data.get("may_modify_routes") is False
        and data.get("may_prove_model_loaded") is False
        and isinstance(rows, list)
        and len(rows) <= 1000
        and type(data.get("provider_count")) is int
        and data["provider_count"] == len(rows)
    )


def check_cutover(origin: str, *, admin_token: str = "", opener=None) -> dict:
    """Return only HTTP status codes and schema verdict, never router data."""
    target = checked_origin(origin)
    results: dict[str, dict[str, object]] = {}
    for path in _ANONYMOUS_PATHS:
        status, _ = _read(target, path, opener=opener)
        results[path] = {"http": status, "expected": 401, "pass": status == 401}

    authorized = "MISSING_OPERATOR_TOKEN"
    if isinstance(admin_token, str) and admin_token.strip():
        status, body = _read(
            target, _EFFECTIVE_PATH, token=admin_token, opener=opener
        )
        authorized = (
            "VALID_SANITIZED_EFFECTIVE_SNAPSHOT"
            if status == 200 and _valid_effective_snapshot(body)
            else "INVALID_OR_UNAVAILABLE"
        )

    ready = all(row["pass"] for row in results.values()) and (
        authorized == "VALID_SANITIZED_EFFECTIVE_SNAPSHOT"
    )
    return {
        "schema": "auto-router-admin-cutover-readonly.v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "READ_ONLY_BOUNDARY_PASS" if ready else "HOLD",
        "anonymous_admin_routes": results,
        "authenticated_effective_catalog": authorized,
        "production_release_authorized": False,
        "physical_model_admission_proven": False,
        "phone_acceptance_proven": False,
        "did_post_or_modify_state": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True, help="Private router HTTP(S) origin")
    args = parser.parse_args()
    try:
        result = check_cutover(
            args.origin, admin_token=os.getenv("AUTO_ROUTER_ADMIN_TOKEN", "")
        )
    except ValueError:
        result = {
            "schema": "auto-router-admin-cutover-readonly.v1",
            "status": "HOLD",
            "reason": "INVALID_PRIVATE_ORIGIN",
            "production_release_authorized": False,
            "did_post_or_modify_state": False,
        }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "READ_ONLY_BOUNDARY_PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
