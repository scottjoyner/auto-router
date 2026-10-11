#!/usr/bin/env python3
"""Read-only, secret-free, loopback-only acceptance for Auto-Router admin hardening.

For supervised post-rollout use. NEVER sends an admin POST, changes routes,
reloads providers, or reports raw body/header/token values.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ANONYMOUS_PATHS = (
    "/admin/providers",
    "/admin/providers/effective",
    "/admin/providers/health",
    "/admin/context",
    "/admin/circuits",
    "/admin/agent-workers",
)
ALLOWED_PROVIDER_FIELDS = frozenset(
    (
        "provider_id", "name", "type", "node_id", "enabled_in_router_memory",
        "effective_base_url", "effective_access_urls", "configured_models",
        "resident_model_identity_verified", "service_reachability_verified",
        "admission_granted",
    )
)
ALLOWED_URL_FIELDS = frozenset(
    ("status", "scheme", "hostname", "port", "userinfo_or_query_present")
)
ALLOWED_MODEL_FIELDS = frozenset(("alias", "provider_model"))


class _DenyRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_loopback_base(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme != "http"
        or parts.hostname not in ("localhost", "127.0.0.1", "::1")
        or parts.port != 8088
        or parts.path not in ("", "/")
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ValueError("only plain loopback HTTP on port 8088 is accepted")
    return value.rstrip("/")


def _get(opener, url: str, *, token: str | None) -> tuple[int, bytes]:
    headers = {"X-Admin-Token": token} if token else {}
    req = Request(url, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=5) as response:
            body = response.read(1_048_577) if token else b""
            return response.status, body
    except HTTPError as error:
        return error.code, b""


def _snapshot_valid(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    if (
        data.get("schema_version") != 1
        or data.get("kind") != "EFFECTIVE_ROUTER_MEMORY_READ_ONLY"
        or data.get("state_source") != "LIVE_ASGI_STATE_NOT_BOOTSTRAP_PROVIDER_FILE"
        or data.get("no_probes_or_dispatch") is not True
        or data.get("may_modify_routes") is not False
        or data.get("may_prove_model_loaded") is not False
    ):
        return False
    rows = data.get("providers")
    if not isinstance(rows, list) or len(rows) > 1000:
        return False
    if data.get("provider_count") != len(rows):
        return False
    for row in rows:
        if not isinstance(row, dict) or not set(row) <= ALLOWED_PROVIDER_FIELDS:
            return False
        if (
            row.get("resident_model_identity_verified") is not False
            or row.get("service_reachability_verified") is not False
            or row.get("admission_granted") is not False
        ):
            return False
        endpoints = [row.get("effective_base_url"), *(row.get("effective_access_urls") or [])]
        if not all(isinstance(p, dict) and set(p) <= ALLOWED_URL_FIELDS for p in endpoints):
            return False
        models = row.get("configured_models")
        if not isinstance(models, list) or len(models) > 100:
            return False
        if not all(isinstance(m, dict) and set(m) <= ALLOWED_MODEL_FIELDS for m in models):
            return False
    return True


def check(base: str, *, token: str | None, opener, anonymous_only: bool = False) -> dict:
    base = validate_loopback_base(base)
    result = {"anonymous_admin_denied": False, "authenticated_snapshot": "NOT_CHECKED"}
    for path in ANONYMOUS_PATHS:
        code, _ = _get(opener, base + path, token=None)
        if code != 401:
            result["anonymous_admin_denied"] = False
            result["reason"] = "ANONYMOUS_ADMIN_NOT_DENIED"
            return result
    result["anonymous_admin_denied"] = True
    if anonymous_only:
        return result
    if not token:
        result["reason"] = "APPROVED_ADMIN_TOKEN_REQUIRED"
        return result
    code, raw = _get(opener, base + "/admin/providers/effective", token=token)
    if code != 200 or len(raw) > 1_048_576:
        result["authenticated_snapshot"] = "FAILED"
        result["reason"] = "AUTHENTICATED_SNAPSHOT_NOT_AVAILABLE"
        return result
    try:
        valid = _snapshot_valid(json.loads(raw))
    except (ValueError, UnicodeError):
        valid = False
    result["authenticated_snapshot"] = "PASS" if valid else "FAILED"
    if not valid:
        result["reason"] = "SNAPSHOT_SCHEMA_OR_REDACTION_INVALID"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--anonymous-only", action="store_true")
    args = parser.parse_args()
    try:
        # Never send an operator credential to a process-wide HTTP proxy.
        # The accepted target is loopback, and redirects remain forbidden.
        opener = build_opener(ProxyHandler({}), _DenyRedirect())
        report = check(
            args.base_url,
            token=os.environ.get("AUTO_ROUTER_ADMIN_TOKEN"),
            opener=opener,
            anonymous_only=args.anonymous_only,
        )
    except (ValueError, URLError, OSError, TimeoutError):
        report = {"anonymous_admin_denied": False, "reason": "LOCAL_ROUTER_UNREACHABLE_OR_INVALID"}
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("anonymous_admin_denied") and (
        args.anonymous_only or report.get("authenticated_snapshot") == "PASS"
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
