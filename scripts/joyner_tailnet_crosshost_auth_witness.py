#!/usr/bin/env python3
"""Source-pinned, credential-free Joyner Tailnet catalog witness from x1-370.

Only four fixed, pre-existing model catalog endpoints are permitted. Never
sends an inference request, reads a response body, follows a redirect,
examines credentials, changes a service, or grants production admission.
A status 200 proves no-key catalog access from this one fleet peer only;
it is not evidence of open internet access or unauthorized model generation.
Always exits 78 (HOLD).
"""
from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SCHEMA = "auto-router.joyner-tailnet-crosshost-auth.v1"
OBSERVER = "x1-370"
HOST = "100.83.215.83"
TIMEOUT_SECONDS = 2.5
ENDPOINTS = (
    ("minicpm-catalog", 1234),
    ("ling-catalog", 1236),
    ("guarded-minicpm-catalog", 18320),
    ("guarded-toolcall-catalog", 18321),
)


@dataclass(frozen=True)
class CatalogTarget:
    name: str
    host: str
    port: int


class _DenyRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """A redirect must NEVER become an unexpected second network target."""
        return None


def observe(target: CatalogTarget) -> tuple[int | None, str | None]:
    """Issue one fixed GET; collect status only, never headers or body."""
    if (target.host != HOST
            or (target.name, target.port) not in ENDPOINTS):
        raise ValueError("unauthorized_catalog_target")
    url = f"http://{HOST}:{target.port}/v1/models"
    opener = build_opener(ProxyHandler({}), _DenyRedirect())
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "joyner-crosshost-catalog-witness/1.0",
        },
    )
    try:
        # The response body and headers are deliberately not inspected.
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, None
    except HTTPError as exc:
        code = exc.code
        exc.close()
        return code, None
    except (URLError, TimeoutError, OSError):
        # Avoid exposing exception strings that can contain host details.
        return None, "transport_unverified"


def classify(status: int | None) -> str:
    if status == 200:
        return "CATALOG_READABLE_WITHOUT_CREDENTIAL"
    if status in (401, 403):
        return "MISSING_CREDENTIAL_REJECTED"
    if status is None:
        return "UNVERIFIED"
    return "UNEXPECTED_HTTP_RESPONSE"


def audit(*, hostname: str | None = None, probe=observe) -> dict:
    actual = (hostname or socket.gethostname()).split(".", 1)[0].lower()
    peer_verified = actual == OBSERVER
    rows = []
    if peer_verified:
        for name, port in ENDPOINTS:
            # Reject any attempt to silently substitute a destination.
            target = CatalogTarget(name=name, host=HOST, port=port)
            try:
                status, error = probe(target)
            except (OSError, ValueError, TypeError):
                status, error = None, "probe_unavailable"
            rows.append({
                "endpoint": name,
                "port": port,
                "http_without_credential": (
                    status if type(status) is int and 100 <= status <= 599 else None
                ),
                "classification": classify(status),
                "error": error if error in (
                    None, "transport_unverified", "probe_unavailable"
                ) else "probe_unavailable",
                "model_generation_tested": False,
                "external_public_reachability_tested": False,
            })
    return {
        "schema": SCHEMA,
        "verdict": "PERIMETER_NO_GO",
        "release_gate": "HOLD",
        "read_only": True,
        "expected_observer": OBSERVER,
        "observer_host_verified": peer_verified,
        "target_is_fixed_existing_joyner_tailnet_ip": True,
        "request_method": "GET",
        "catalog_only": True,
        "redirects_followed": False,
        "response_bodies_read": False,
        "credentials_sent": False,
        "model_generation_performed": False,
        "tailnet_peer_reachability_observed": (
            peer_verified and any(
                r["http_without_credential"] in (200, 401, 403) for r in rows
            )
        ),
        "unauthenticated_catalogs_observed": [
            r["endpoint"] for r in rows
            if r["classification"] == "CATALOG_READABLE_WITHOUT_CREDENTIAL"
        ],
        "guarded_catalogs_observed": [
            r["endpoint"] for r in rows
            if r["classification"] == "MISSING_CREDENTIAL_REJECTED"
        ],
        "unauthorized_client_reachability_verified": False,
        "public_internet_reachability_verified": False,
        "service_change_authorized": False,
        "router_promotion_authorized": False,
        "gpu_allocation_authorized": False,
        "endpoints": rows,
    }


def main() -> int:
    print(json.dumps(audit(), sort_keys=True, separators=(",", ":")))
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
