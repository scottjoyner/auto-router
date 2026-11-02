#!/usr/bin/env python3
"""Exhaustive negative-auth check against GitHub CI's isolated, offline Router.

Not a production diagnostic. Never send authenticated administrative POSTs.
Both expected route identity and the live OpenAPI surface must agree exactly.
Only status codes and non-sensitive aggregate booleans are emitted.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8088"
EXPECTED = frozenset({
    ("GET", "/admin/quota"),
    ("GET", "/admin/providers/health"),
    ("GET", "/admin/context"),
    ("GET", "/admin/context/graph"),
    ("GET", "/admin/providers"),
    ("GET", "/admin/providers/effective"),
    ("GET", "/admin/placement"),
    ("GET", "/admin/agent-workers"),
    ("GET", "/admin/usage"),
    ("GET", "/admin/circuits"),
    ("GET", "/admin/agent-jobs"),
    ("GET", "/admin/admission"),
    ("GET", "/admin/runtime-projection"),
    ("GET", "/admin/ops/summary"),
    ("GET", "/admin/ops/preflight"),
    ("GET", "/admin/memory"),
    ("POST", "/admin/load"),
    ("POST", "/admin/circuits/reset"),
    ("POST", "/admin/circuits/reset/{owner}"),
    ("POST", "/admin/runtime-projection"),
})
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
WRONG = "known-incorrect-ci-token"


class AdminSurfaceDenied(RuntimeError):
    pass


def require_ci() -> None:
    if (os.getenv("GITHUB_ACTIONS") != "true"
            or os.getenv("AUTO_ROUTER_CANARY_ENV") != "disposable-github-ci"
            or os.getenv("AUTO_ROUTER_CANARY_SYNTHETIC_ADMIN_TOKEN") != "smoke-admin-token"):
        raise AdminSurfaceDenied("exhaustive route probe restricted to disposable GitHub CI")


def request_status(method: str, template: str, token: str | None = None,
                   *, read_schema: bool = False) -> tuple[int, bytes]:
    require_ci()
    if (method, template) not in EXPECTED and not (
            method == "GET" and template == "/openapi.json" and read_schema):
        raise AdminSurfaceDenied("route not approved for synthetic check")
    if read_schema and template != "/openapi.json":
        raise AdminSurfaceDenied("refusing administrative response content")
    path = template.replace("{owner}", "ci-fixture-owner")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", path):
        raise AdminSurfaceDenied("invalid checked route identity")
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["X-Admin-Token"] = token
    if method == "POST":
        headers["Content-Type"] = "application/json"
    req = Request(BASE + path, headers=headers,
                  data=b"{}" if method == "POST" else None, method=method)
    try:
        with urlopen(req, timeout=5) as response:
            # No administrative response body is ever read, even on auth bypass.
            payload = response.read(256_001) if read_schema else b""
            if read_schema and len(payload) > 256_000:
                raise AdminSurfaceDenied("unbounded OpenAPI schema")
            return response.status, payload
    except HTTPError as error:
        error.close()
        return error.code, b""
    except (URLError, TimeoutError) as error:
        raise AdminSurfaceDenied("disposable Router unavailable") from error


def admitted_operations(raw: bytes) -> frozenset[tuple[str, str]]:
    if not raw or len(raw) > 256_000:
        raise AdminSurfaceDenied("missing or excessive OpenAPI source")
    try:
        doc = json.loads(raw)
    except ValueError as error:
        raise AdminSurfaceDenied("invalid OpenAPI schema") from error
    if not isinstance(doc, dict) or not isinstance(doc.get("paths"), dict):
        raise AdminSurfaceDenied("OpenAPI operation table missing")
    operations: set[tuple[str, str]] = set()
    for route, methods in doc["paths"].items():
        if not isinstance(route, str) or not isinstance(methods, dict):
            raise AdminSurfaceDenied("invalid OpenAPI route entry")
        if not route.startswith("/admin/"):
            continue
        for method in methods:
            if method.lower() in HTTP_METHODS:
                operation = (method.upper(), route)
                if operation in operations:
                    raise AdminSurfaceDenied("duplicate admin operation")
                operations.add(operation)
            elif method != "parameters":
                raise AdminSurfaceDenied("unknown administrative method")
    if operations != EXPECTED:
        raise AdminSurfaceDenied("mounted admin operations differ from audited expected surface")
    return frozenset(operations)


def main() -> None:
    require_ci()
    code, raw = request_status("GET", "/openapi.json", read_schema=True)
    if code != 200:
        raise AdminSurfaceDenied("OpenAPI unavailable in disposable Router")
    operations = admitted_operations(raw)
    checked_get = checked_post = 0
    for method, path in sorted(operations):
        for token in (None, WRONG):
            status, _ = request_status(method, path, token)
            if status != 401:
                raise AdminSurfaceDenied("administrative authentication bypass or unexpected response")
        checked_get += method == "GET"
        checked_post += method == "POST"
    if checked_get != 16 or checked_post != 4:
        raise AdminSurfaceDenied("incomplete admin method coverage")
    print(json.dumps({
        "verdict": "DISPOSABLE_ALL_ADMIN_AUTH_NEGATIVES_PASS",
        "mounted_admin_operations": len(operations),
        "get_operations": checked_get,
        "post_operations": checked_post,
        "checked_missing_and_wrong_credentials": True,
        "authenticated_mutation_attempts": 0,
        "production_tested": False,
        "production_modified": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
