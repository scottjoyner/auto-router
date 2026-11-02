#!/usr/bin/env python3
"""Read-only legacy-client parity against disposable strict-offline Router CI.

Only fixed loopback GET requests; never contacts real providers or production.
No payloads, credentials, hostnames, or configuration are emitted.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit

if __package__:
    from .verify_admin_canary_ci import assert_redacted
else:
    from verify_admin_canary_ci import assert_redacted

BASE = "http://127.0.0.1:8088"
PUBLIC = ("/health", "/v1/models")
ADMIN = ("/admin/context", "/admin/providers", "/admin/runtime-projection",
         "/admin/providers/effective")
WRONG = "known-wrong-synthetic-client-token"
TOKEN = "smoke-admin-token"


class ClientParityDenied(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def require_disposable_ci():
    if (os.getenv("GITHUB_ACTIONS") != "true"
            or os.getenv("AUTO_ROUTER_CANARY_ENV") != "disposable-github-ci"
            or os.getenv("AUTO_ROUTER_CANARY_SYNTHETIC_ADMIN_TOKEN") != TOKEN):
        raise ClientParityDenied("client parity requires disposable GitHub CI")


def read_status(path: str, auth: str = "none", *, body: bool = False) -> tuple[int, bytes]:
    require_disposable_ci()
    if path not in PUBLIC + ADMIN or auth not in ("none", "wrong", "header", "bearer", "basic"):
        raise ClientParityDenied("unapproved client request")
    if body and auth not in ("none", "header"):
        raise ClientParityDenied("refuse other response payloads")
    if body and auth == "none" and path not in PUBLIC:
        raise ClientParityDenied("refuse anonymous admin response content")
    headers = {"Accept": "application/json"}
    if auth in ("header", "wrong"):
        headers["X-Admin-Token"] = TOKEN if auth == "header" else WRONG
    elif auth == "bearer":
        headers["Authorization"] = "Bearer " + TOKEN
    elif auth == "basic":
        headers["Authorization"] = "Basic " + base64.b64encode(
            ("admin:" + TOKEN).encode("ascii")).decode("ascii")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect())
    req = urllib.request.Request(BASE + path, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=4) as response:
            data = response.read(131073) if body else b""
            if len(data) > 131072:
                raise ClientParityDenied("response exceeds synthetic bound")
            return response.status, data
    except urllib.error.HTTPError as exc:
        exc.close()
        return exc.code, b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ClientParityDenied("disposable loopback unavailable") from exc


def json_object(raw: bytes) -> dict:
    if not 0 < len(raw) <= 131072:
        raise ClientParityDenied("missing or oversized read-only response")
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ClientParityDenied("synthetic client response not JSON") from exc
    if not isinstance(value, dict):
        raise ClientParityDenied("synthetic response not an object")
    return value


def verify_legacy_provider_catalog(raw: bytes) -> int:
    """Minimal shape actually consumed by AssistX control-room catalog."""
    doc = json_object(raw)
    providers = doc.get("providers")
    if not isinstance(providers, list) or not 1 <= len(providers) <= 1000:
        raise ClientParityDenied("provider list incompatible with legacy client")
    for row in providers:
        if not isinstance(row, dict) or type(row.get("enabled")) is not bool:
            raise ClientParityDenied("legacy provider enabled field missing")
        if not isinstance(row.get("models"), list):
            raise ClientParityDenied("legacy provider model array missing")
        if len(row["models"]) > 100:
            raise ClientParityDenied("unbounded model inventory")
        if not isinstance(row.get("node_id"), (str, type(None))):
            raise ClientParityDenied("legacy provider node identity malformed")
        if not isinstance(row.get("runtime_instance_id"), (str, type(None))):
            raise ClientParityDenied("legacy runtime identity malformed")
        url = row.get("base_url")
        if not isinstance(url, str) or len(url) > 256:
            raise ClientParityDenied("legacy hostname URL unavailable")
        endpoint = urlsplit(url)
        if (endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1"
                or endpoint.port != 9 or endpoint.username or endpoint.password
                or endpoint.query or endpoint.fragment):
            raise ClientParityDenied("non-synthetic provider URL in CI fixture")
        if (row.get("api_key_env") is not None or row.get("api_key_file") is not None
                or row.get("headers") not in ({}, None) or row["models"]):
            raise ClientParityDenied("unexpected provider credential or model in CI fixture")
    return len(providers)


def main():
    require_disposable_ci()
    for path in PUBLIC:
        status, _ = read_status(path)
        if status != 200:
            raise ClientParityDenied("public client discovery regression")
    for path in ADMIN:
        for mode in ("none", "wrong"):
            status, _ = read_status(path, mode)
            if status != 401:
                raise ClientParityDenied("legacy administrative authentication denial failed")
    checks = {}
    for path in ADMIN:
        status, body = read_status(path, "header", body=True)
        if status != 200:
            raise ClientParityDenied("synthetic legacy admin-header read failed")
        checks[path] = body
    if not isinstance(json_object(checks["/admin/context"]), dict):
        raise ClientParityDenied("legacy context body unavailable")
    count = verify_legacy_provider_catalog(checks["/admin/providers"])
    projection = json_object(checks["/admin/runtime-projection"])
    if (projection.get("configured") is not False or
            projection.get("current") is not None):
        raise ClientParityDenied("disposable runtime projection unexpectedly active")
    sanitized = assert_redacted(checks["/admin/providers/effective"])
    if sanitized.get("sanitized") is not True:
        raise ClientParityDenied("effective provider redaction denied")
    for mode in ("bearer", "basic"):
        status, _ = read_status("/admin/providers", mode)
        if status != 200:
            raise ClientParityDenied("legacy admin authorization mechanism regressed")
    print(json.dumps({
        "verdict": "DISPOSABLE_LEGACY_CLIENT_READ_PARITY_PASS",
        "public_discovery_reads": len(PUBLIC),
        "admin_missing_wrong_denials": len(ADMIN) * 2,
        "authorized_legacy_admin_reads": len(ADMIN),
        "alternate_authorization_reads": 2,
        "legacy_catalog_shape_verified": True,
        "bootstrap_unadmitted_provider_count": count,
        "sanitized_effective_providers": True,
        "model_requests": 0,
        "authenticated_admin_mutations": 0,
        "production_tested": False,
        "production_modified": False,
        "cross_repo_real_clients_executed": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
