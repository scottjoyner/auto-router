#!/usr/bin/env python3
"""Fail-closed HTTP canary for the disposable CI Router image only.

Never test production from this script. The caller must set the explicit
disposable-CI sentinel and use the loopback service started by integration-smoke.
It makes ONLY GET requests and prints status/evidence booleans, never bodies,
credentials, provider URLs, or response headers.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8088"
CHECKED = (
    "/admin/providers",
    "/admin/circuits",
    "/admin/providers/effective",
    "/admin/quota",
    "/admin/admission",
)
FORBIDDEN_KEYS = {
    "headers", "api_key_env", "base_url", "access_urls",
    "authorization", "password", "credential", "secret",
}
REQUIRED_EFFECTIVE = {
    "schema_version", "kind", "provider_count", "providers",
    "state_source", "no_probes_or_dispatch",
}

class CanaryRejected(RuntimeError):
    pass

def get_status(path: str, token: str | None = None, *, read_body: bool = False):
    if path not in CHECKED:
        raise CanaryRejected("unapproved GET path")
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["X-Admin-Token"] = token
    req = Request(BASE + path, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=5) as resp:
            data = resp.read(256_000) if read_body else b""
            return resp.status, data
    except HTTPError as exc:
        exc.close()
        return exc.code, b""
    except (URLError, TimeoutError) as exc:
        raise CanaryRejected("loopback Router unavailable") from exc

def assert_redacted(payload: bytes) -> dict[str, int | bool]:
    if not payload or len(payload) > 256_000:
        raise CanaryRejected("missing or oversized provider snapshot")
    doc = json.loads(payload)
    if not isinstance(doc, dict) or not REQUIRED_EFFECTIVE <= set(doc):
        raise CanaryRejected("missing sanitized provider schema")
    if doc["kind"] != "EFFECTIVE_ROUTER_MEMORY_READ_ONLY":
        raise CanaryRejected("unexpected snapshot identity")
    if doc["no_probes_or_dispatch"] is not True:
        raise CanaryRejected("provider snapshot may perform runtime activity")
    if type(doc["provider_count"]) is not int or not 0 <= doc["provider_count"] <= 1000:
        raise CanaryRejected("invalid provider count")
    rows = doc["providers"]
    if not isinstance(rows, list) or len(rows) != doc["provider_count"]:
        raise CanaryRejected("invalid inventory")
    # Keys are allowed only from a narrow, non-secret response projection.
    def verify_object(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if str(k).lower() in FORBIDDEN_KEYS:
                    raise CanaryRejected("forbidden provider secret field")
                verify_object(v)
        elif isinstance(value, list):
            for item in value:
                verify_object(item)
    verify_object(doc)
    for row in rows:
        if not isinstance(row, dict):
            raise CanaryRejected("invalid provider row")
        for flag in ("resident_model_identity_verified",
                     "service_reachability_verified", "admission_granted"):
            if row.get(flag) is not False:
                raise CanaryRejected("unauthorized runtime assurance claim")
        for field in ("effective_base_url",):
            endpoint = row.get(field)
            if not isinstance(endpoint, dict):
                raise CanaryRejected("endpoint projection missing")
            if set(endpoint) - {
                "status", "scheme", "hostname", "port",
                "userinfo_or_query_present",
            }:
                raise CanaryRejected("endpoint projection contains raw URI field")
    return {"provider_count": doc["provider_count"], "sanitized": True}

def main():
    if os.environ.get("AUTO_ROUTER_CANARY_ENV") != "disposable-github-ci":
        raise CanaryRejected("this verifier is permitted only in disposable GitHub CI")
    token = os.environ.get("AUTO_ROUTER_CANARY_SYNTHETIC_ADMIN_TOKEN", "")
    if token != "smoke-admin-token":
        raise CanaryRejected("CI token must match known disposable fixture")
    results = []
    for path in CHECKED:
        no_token, _ = get_status(path)
        bad_token, _ = get_status(path, token="known-incorrect-ci-token")
        if no_token != 401 or bad_token != 401:
            raise CanaryRejected("administrative authentication bypass: " + path)
        results.append((path, no_token, bad_token))
    for path in ("/admin/providers", "/admin/providers/effective", "/admin/quota"):
        status, body = get_status(path, token, read_body=(path == "/admin/providers/effective"))
        if status != 200:
            raise CanaryRejected("authorized admin GET failed: " + path)
        if path == "/admin/providers/effective":
            snapshot = assert_redacted(body)
    print(json.dumps({
        "result": "DISPOSABLE_FULL_ASGI_ADMIN_AUTH_PASS",
        "checked_routes": len(results),
        "denied_missing_and_wrong_token": True,
        "authorized_read_pass": True,
        "effective_snapshot": snapshot,
        "production_tested": False,
        "production_modified": False,
        "synthetic_credentials_only": True,
    }, sort_keys=True))

if __name__ == "__main__":
    main()
