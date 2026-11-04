#!/usr/bin/env python3
"""Strict read-only, loopback-only deployed Auto-Router admin authorization canary.

No bearer token, no secret, no response-body read, no POST or side effects.
Designed to fail before cutover and pass only after *deployed* anonymous
admin GET requests are denied. Run as a human-approved rollout gate.
"""
from __future__ import annotations

import argparse
import http.client
import json

ADMIN_PATHS = (
    "/admin/providers",
    "/admin/context",
    "/admin/providers/health",
    "/admin/circuits",
    "/admin/agent-jobs",
    "/admin/admission",
)


def probe(port: int, paths: tuple[str, ...] = ADMIN_PATHS) -> dict:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid loopback HTTP port")
    if not paths or any(path not in ADMIN_PATHS for path in paths):
        raise ValueError("only predeclared read-only admin paths are permitted")
    results = []
    for path in paths:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET", path, headers={
                "Accept": "application/json",
                "User-Agent": "auto-router-security-no-body-read-canary",
                "Connection": "close",
            })
            response = connection.getresponse()
            status = response.status
            # Intentionally DO NOT call response.read(), even on HTTP 200.
            results.append({"path": path, "status": status,
                            "anonymous_denied": status == 401})
        except (OSError, http.client.HTTPException) as error:
            results.append({"path": path, "status": None,
                            "anonymous_denied": False,
                            "error_type": type(error).__name__})
        finally:
            connection.close()
    return {
        "schema": "auto-router.deployed-admin-unauthenticated-canary.v1",
        "read_only": True,
        "loopback_only": True,
        "response_bodies_read": False,
        "authenticated_requests_sent": False,
        "production_write": False,
        "all_denied": all(x["anonymous_denied"] for x in results),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=8088)
    args = parser.parse_args()
    result = probe(args.port)
    print(json.dumps(result,sort_keys=True))
    raise SystemExit(0 if result["all_denied"] else 2)


if __name__ == "__main__":
    main()
