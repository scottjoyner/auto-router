#!/usr/bin/env python3
"""Read-only, redacted live x1 Router/AssistX client credential-parity preflight.

DO NOT treat successful local configuration equivalence as authentication,
network reachability, deployment readiness, or rollback verification.
Only Docker inspect is permitted. No HTTP request, exec, Docker write, or secret output.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
from urllib.parse import urlsplit

ROUTER = "git-router-1"
CLIENTS = ("assistx-api", "assistx-worker", "assistx-hermes-adapter")
IMAGE_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
CREDENTIAL = "AUTO_ROUTER_ADMIN_TOKEN"


class PreflightDenied(RuntimeError):
    pass


def read_docker_inspect(name: str) -> dict:
    if name not in (ROUTER, *CLIENTS):
        raise PreflightDenied("unapproved Docker container identity")
    try:
        completed = subprocess.run(
            ["docker", "inspect", name], capture_output=True, text=True,
            check=False, timeout=8,
        )
        if completed.returncode != 0 or len(completed.stdout) > 2_000_000:
            raise PreflightDenied("Docker metadata unavailable")
        payload = json.loads(completed.stdout)
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise PreflightDenied("malformed Docker metadata")
        data = payload[0]
        if data.get("Name") != "/" + name:
            raise PreflightDenied("wrong Docker container identity")
        return data
    except (subprocess.TimeoutExpired, OSError, ValueError, TypeError):
        # Never include subprocess output or secret-bearing Docker metadata.
        raise PreflightDenied("Docker inspection rejected") from None


def parse_sensitive_env(container: dict) -> dict[str, str]:
    environment = (container.get("Config") or {}).get("Env")
    if not isinstance(environment, list) or len(environment) > 250:
        raise PreflightDenied("invalid environment metadata")
    allowed = {CREDENTIAL, "AUTO_ROUTER_BASE_URL",
               "AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED"}
    found: dict[str, str] = {}
    for value in environment:
        if not isinstance(value, str) or "=" not in value:
            raise PreflightDenied("invalid Docker environment item")
        key, _, raw = value.partition("=")
        if key in allowed:
            if key in found:
                raise PreflightDenied("duplicate sensitive environment setting")
            found[key] = raw
    return found


def safe_client_url(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 256:
        return False
    try:
        parsed = urlsplit(value.strip())
        return (
            parsed.scheme == "http" and
            parsed.hostname == "host.docker.internal" and
            parsed.port == 8088 and
            parsed.path in ("", "/") and
            not parsed.username and not parsed.password and
            not parsed.query and not parsed.fragment and
            parsed.netloc == "host.docker.internal:8088"
        )
    except ValueError:
        return False


def has_host_mapping(container: dict) -> bool:
    hosts = (container.get("HostConfig") or {}).get("ExtraHosts") or []
    return (isinstance(hosts, list) and
            any(isinstance(x, str) and x.startswith("host.docker.internal:")
                and x.count(":") >= 1 for x in hosts))


def is_running(container: dict) -> bool:
    return (container.get("State") or {}).get("Status") == "running"


def evaluate(inventories: dict[str, dict], expected_image: str) -> dict:
    if not IMAGE_RE.fullmatch(expected_image):
        raise PreflightDenied("expected current production image must be pinned")
    if set(inventories) != {ROUTER, *CLIENTS}:
        raise PreflightDenied("container inventory incomplete")
    r = inventories[ROUTER]
    router_env = parse_sensitive_env(r)
    token = router_env.get(CREDENTIAL, "").strip()
    issues: list[str] = []
    if r.get("Image") != expected_image:
        issues.append("ROUTER_IMAGE_DRIFT")
    if not is_running(r):
        issues.append("ROUTER_NOT_RUNNING")
    if router_env.get("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED") != "true":
        issues.append("EXECUTOR_AUTH_NOT_REQUIRED")
    if not token:
        issues.append("ROUTER_ADMIN_TOKEN_MISSING")
    ports = ((r.get("HostConfig") or {}).get("PortBindings") or {}).get("8088/tcp") or []
    if not isinstance(ports, list) or not any(
        isinstance(p, dict) and p.get("HostPort") == "8088" for p in ports
    ):
        issues.append("ROUTER_PORT_CONTRACT_DRIFT")
    result_clients = []
    for name in CLIENTS:
        client = inventories[name]
        env = parse_sensitive_env(client)
        client_token = env.get(CREDENTIAL, "").strip()
        match = bool(token and client_token and secrets.compare_digest(token, client_token))
        url_matches = safe_client_url(env.get("AUTO_ROUTER_BASE_URL"))
        mapped = has_host_mapping(client)
        running = is_running(client)
        if not match:
            issues.append("CLIENT_ADMIN_TOKEN_MISMATCH:" + name)
        if not url_matches:
            issues.append("CLIENT_ROUTER_TARGET_DRIFT:" + name)
        if not mapped:
            issues.append("CLIENT_HOST_GATEWAY_ALIAS_MISSING:" + name)
        if not running:
            issues.append("CLIENT_NOT_RUNNING:" + name)
        result_clients.append({
            "service": name,
            "admin_token_matches_router": match,
            "configured_router_target_matches_expected": url_matches,
            "docker_host_gateway_mapping_present": mapped,
            "running": running,
        })
    return {
        "schema": 1,
        "verdict": ("CLIENT_CONFIG_PARITY_OBSERVED_HOLD" if not issues
                    else "CLIENT_CONFIG_PARITY_DRIFT_HOLD"),
        "reasons": sorted(issues),
        "checked_running_image_matches_pinned": r.get("Image") == expected_image,
        "running_router_admin_token_present": bool(token),
        "observed_client_count": len(result_clients),
        "clients": result_clients,
        "read_only": True,
        "credentials_never_emitted": True,
        "no_http_requests_made": True,
        "authenticated_client_traffic_verified": False,
        "offhost_tailnet_auth_verified": False,
        "real_inference_client_compatibility_verified": False,
        "production_cutover_allowed": False,
        "production_modified": False,
        "rollback_verified": False,
        "independent_custody_verified": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Sanitized, strictly read-only cutover preflight")
    p.add_argument("--expected-running-image", required=True)
    args = p.parse_args()
    try:
        records = {name: read_docker_inspect(name) for name in (ROUTER, *CLIENTS)}
        report = evaluate(records, args.expected_running_image)
    except PreflightDenied:
        print(json.dumps({
            "schema": 1, "verdict": "CLIENT_CONFIG_PARITY_UNAVAILABLE_HOLD",
            "production_cutover_allowed": False, "production_modified": False,
            "credentials_never_emitted": True,
        }, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if not report["reasons"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
