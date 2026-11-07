#!/usr/bin/env python3
"""Offline no-lifespan authentication contract for a fully mounted FastAPI app.

Research-only. Does NOT start the uvicorn server, issue network traffic,
exercise authenticated mutations, or print any token/configuration secrets.
"""
from __future__ import annotations

import argparse
import json
import re

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from auto_router.security import require_admin


def _path_template(path: str) -> str:
    return re.sub(r"\{[^{}]+\}", "synthetic-route-audit", path)


def audit(app) -> dict:
    mounted = [route for route in app.routes
               if isinstance(route, APIRoute)
               and route.path.startswith("/admin/")]
    if not mounted:
        raise ValueError("application contains no mounted admin routes")
    methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    checked = []
    with_no_lifespan = TestClient(app, raise_server_exceptions=False,
                                 follow_redirects=False)
    try:
        for route in mounted:
            guarded = any(dep.call is require_admin
                          for dep in route.dependant.dependencies)
            if not guarded:
                raise AssertionError(f"missing compiled admin guard: {route.path}")
            for method in sorted(route.methods & methods):
                response = with_no_lifespan.request(
                    method, _path_template(route.path),
                    headers={"Accept": "application/json"},
                )
                if response.status_code != 401:
                    raise AssertionError(
                        f"admin route accepted anonymous request: "
                        f"{method} {route.path} -> {response.status_code}"
                    )
                checked.append({"method": method, "path": route.path,
                                "status": response.status_code})
    finally:
        with_no_lifespan.close()
    if not checked:
        raise AssertionError("no HTTP admin methods were audited")
    return {
        "schema": "auto-router.synthetic-admin-mounted-gate.v1",
        "research_only": True,
        "production_status": "NO_GO",
        "no_lifespan": True,
        "network_calls": False,
        "authenticated_mutations": False,
        "mounted_routes": len(mounted),
        "checked_method_paths": len(checked),
        "denied": checked,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entrypoint",choices=["main", "main_live", "secure_live"],required=True)
    args = ap.parse_args()
    if args.entrypoint == "secure_live":
        # This is the actual container ASGI entrypoint; no lifespan.
        from auto_router.secure_live import app
    elif args.entrypoint == "main_live":
        from auto_router.main_live import app
    else:
        from auto_router.main import app
    result = audit(app)
    result["entrypoint"] = args.entrypoint
    print(json.dumps(result,sort_keys=True))


if __name__ == "__main__":
    main()
