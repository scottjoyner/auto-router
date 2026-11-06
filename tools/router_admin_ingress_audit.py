#!/usr/bin/env python3
"""Non-mutating anonymous admin ingress checks for a pinned Router candidate.

The caller must supply every ingress origin to check, including IPv4, IPv6,
Docker-peer, and other approved network contexts. No credentials are accepted,
no HTTP methods except GET are sent, response bodies are never read/printed,
proxies and redirects are disabled, and release approval is never issued.

A PASS is evidence for the specified origins only, not proof of image custody,
operator access, firewall reachability elsewhere, rollback, or production GO.
"""
from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ADMIN_GETS = (
    "/admin/providers",
    "/admin/context",
    "/admin/agent-jobs",
)

# Pinned exact effective GET inventory from PR #135 secure_live mounted ASGI.
# A missing route (404) is not an authentication pass. Never infer coverage
# from dormant source registrars that aren't present in this exact build.
# The paired real-ASGI test checks these paths against the mounted app.
EXTENDED_ADMIN_GETS = ADMIN_GETS + (
    "/admin/quota",
    "/admin/providers/health",
    "/admin/context/graph",
    "/admin/placement",
    "/admin/agent-workers",
    "/admin/usage",
    "/admin/circuits",
    "/admin/admission",
    "/admin/runtime-projection",
    "/admin/ops/summary",
    "/admin/ops/preflight",
    "/admin/memory",
)

# Include POST paths for *offline* candidate route-identity assertions only.
# The network ingress audit sends GET requests exclusively.
EXPECTED_ADMIN_METHODS = frozenset(
    [("GET", path) for path in EXTENDED_ADMIN_GETS] + [
        ("POST", "/admin/load"),
        ("POST", "/admin/circuits/reset"),
        ("POST", "/admin/circuits/reset/guard-probe"),
        ("POST", "/admin/runtime-projection"),
    ]
)

class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def normalize_target(raw: str) -> str:
    """Reject implicit credentials, paths and destinations outside one origin."""
    parts = urlsplit(raw)
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.path not in ("", "/") or parts.query or parts.fragment
            or any(ch.isspace() for ch in raw)):
        raise ValueError("target must be a credential-free HTTP(S) origin")
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError("invalid target port") from exc
    if not parts.netloc or "@" in parts.netloc:
        raise ValueError("ambiguous target authority")
    return f"{parts.scheme}://{parts.netloc}"


def probe_status(origin: str, path: str, timeout: float) -> tuple[int | None, str | None]:
    """Single direct unauthenticated GET, never following a redirect/proxy."""
    opener = build_opener(ProxyHandler({}), _RejectRedirects())
    request = Request(
        origin + path,
        headers={"Accept": "application/json", "Cache-Control": "no-store"},
        method="GET",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, None
    except HTTPError as exc:
        # No body reads: an untrusted target may include credentials in errors.
        return exc.code, None
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        return None, type(exc).__name__


def audit(origins: list[str], timeout: float = 3.0,
          paths: tuple[str, ...] = ADMIN_GETS) -> dict:
    """HOLD always, including when every explicitly provided probe passes."""
    reports = []
    for origin in origins:
        results = []
        for path, allowed in [("/health", (200,))] + [
            (path, (401, 403)) for path in paths
        ]:
            status, error = probe_status(origin, path, timeout)
            results.append({
                "path": path,
                "status": status,
                "result": "PASS" if status in allowed else "FAIL",
                "error_type": error,
            })
        reports.append({"origin": origin, "probes": results})
    passed = bool(reports) and all(
        probe["result"] == "PASS"
        for report in reports for probe in report["probes"]
    )
    return {
        "schema": "router-anonymous-admin-ingress-v1",
        "read_only": True,
        "credentials_sent": False,
        "probe_result": "PASS" if passed else "FAIL",
        "release_decision": "HOLD",
        "origins": reports,
        "admin_paths_checked": list(paths),
        "limitations": (
            "Only listed origins tested; no Docker peer/WAN/IPv6 coverage is implied. "
            "No authenticated positive smoke, signer custody, rollback, or deployment authority."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", required=True, metavar="URL",
                        help="Exact credential-free origin. Repeat for each distinct ingress path.")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--extended", action="store_true",
                        help="Probe all 15 mounted candidate admin GET paths; absent paths fail closed.")
    args = parser.parse_args(argv)
    if not 0.1 <= args.timeout <= 10.0:
        parser.error("timeout must be between 0.1 and 10 seconds")
    try:
        origins = [normalize_target(target) for target in args.target]
    except ValueError as exc:
        parser.error(str(exc))
    if len(set(origins)) != len(origins):
        parser.error("duplicate origins cannot substitute for independent ingress paths")
    result = audit(origins, timeout=args.timeout,
                   paths=EXTENDED_ADMIN_GETS if args.extended else ADMIN_GETS)
    json.dump(result, sys.stdout, sort_keys=True, indent=2)
    sys.stdout.write("\n")
    return 0 if result["probe_result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
