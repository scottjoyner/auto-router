#!/usr/bin/env python3
"""Strict synthetic CI-only admin-denial check from a separate Docker peer.

No tokens, no request bodies, and no administrative writes. Restricted to the
CI Compose service alias. Never treats fixture success as production admission.
"""
from __future__ import annotations

import http.client
import json
import os

HOST = "llm-router"
PORT = 8088
SAFE_PATHS = (
    "/admin/providers",
    "/admin/context",
    "/admin/circuits",
    "/admin/agent-jobs",
    "/admin/admission",
)


def _status(path: str, *, connector=http.client.HTTPConnection) -> int | None:
    if path not in ("/health", *SAFE_PATHS):
        raise ValueError("non-safelisted endpoint")
    conn = connector(HOST, PORT, timeout=3)
    try:
        conn.request("GET", path, headers={
            "Accept": "application/json",
            "Connection": "close",
        })
        return conn.getresponse().status  # No response.read().
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def verify(*, status=_status) -> dict:
    # A deny-all service or a broken reverse proxy could also yield 401;
    # require a 200 for the health path on the same service identity.
    healthy = status("/health") == 200
    admin_denied = [status(path) == 401 for path in SAFE_PATHS]
    return {
        "schema": "auto-router.synthetic-docker-peer-denial.v1",
        "same_network_health_200": healthy,
        "five_safe_admin_gets_401": all(admin_denied),
        "ci_only": True,
        "read_only": True,
        "secrets_provided": False,
        "response_bodies_read": False,
        "production_admission": "HOLD",
        "checks_passed": healthy and all(admin_denied),
    }


def main() -> int:
    if not (
        os.getenv("CI") == "true"
        and os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("AUTO_ROUTER_CI_DOCKER_PEER") == "true"
    ):
        print(json.dumps({
            "schema": "auto-router.synthetic-docker-peer-denial.v1",
            "checks_passed": False,
            "production_admission": "HOLD",
        }, sort_keys=True))
        return 2
    proof = verify()
    print(json.dumps(proof, sort_keys=True))
    return 0 if proof["checks_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
