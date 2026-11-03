#!/usr/bin/env python3
"""Read-only, value-free topology witness for emergency router ingress containment.

A status/HOLD report, NEVER an authorization or a firewall plan. Docker port
publishing and actual forward/NAT paths must be approved by a security owner.
Never emits Docker config/env values, host IPs, filenames or firewall rules.
"""
from __future__ import annotations

import json
import re
import subprocess
from urllib.parse import urlsplit

NAMES = ("git-router-1", "assistx-api", "assistx-worker", "assistx-hermes-adapter")
PORT = "8088/tcp"


def _safe_origin(env_items: object) -> dict[str, object]:
    values: dict[str, str] = {}
    if isinstance(env_items, list):
        for entry in env_items:
            if isinstance(entry, str) and "=" in entry:
                name, value = entry.split("=", 1)
                if name in ("AUTO_ROUTER_ADMIN_TOKEN", "AUTO_ROUTER_BASE_URL"):
                    values[name] = value
    result: dict[str, object] = {
        "admin_token_configured": bool(values.get("AUTO_ROUTER_ADMIN_TOKEN", "").strip()),
        "scheme": "INVALID",
        "configured_host_present": False,
        "router_port_valid": False,
        "userinfo_or_query_present": False,
    }
    try:
        parts = urlsplit(values.get("AUTO_ROUTER_BASE_URL", ""))
        result["scheme"] = parts.scheme.upper() if parts.scheme in ("http", "https") else "INVALID"
        result["configured_host_present"] = bool(parts.hostname)
        result["router_port_valid"] = parts.port == 8088
        result["userinfo_or_query_present"] = bool(
            parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/")
        )
    except (ValueError, TypeError):
        pass
    return result


def summarize_containers(containers: object, hook_readability: object) -> dict[str, object]:
    reasons: list[str] = []
    nodes: dict[str, dict] = {}
    if isinstance(containers, list):
        for item in containers:
            if not isinstance(item, dict):
                continue
            name = item.get("Name")
            if isinstance(name, str) and name.lstrip("/") in NAMES:
                nodes[name.lstrip("/")] = item
    missing = [n for n in NAMES if n not in nodes]
    if missing:
        reasons.append("EXPECTED_SERVICE_METADATA_MISSING")

    router = nodes.get("git-router-1", {})
    router_networks = set(
        (router.get("NetworkSettings", {}).get("Networks") or {}).keys()
    )
    bindings = (router.get("NetworkSettings", {}).get("Ports") or {}).get(PORT) or []
    wildcard_v4 = any(
        isinstance(entry, dict) and entry.get("HostIp") in ("0.0.0.0", "")
        for entry in bindings
    )
    wildcard_v6 = any(
        isinstance(entry, dict) and entry.get("HostIp") in ("::", "[::]")
        for entry in bindings
    )
    if wildcard_v4 or wildcard_v6:
        reasons.append("ROUTER_PUBLISHED_ON_WILDCARD_HOST_ADDRESS")

    consumers: dict[str, dict[str, object]] = {}
    for name in NAMES[1:]:
        item = nodes.get(name, {})
        nets = set((item.get("NetworkSettings", {}).get("Networks") or {}).keys())
        origin = _safe_origin((item.get("Config") or {}).get("Env"))
        state = (item.get("State") or {}).get("Running") is True
        overlap = bool(nets & router_networks)
        if not state or not origin["admin_token_configured"]:
            reasons.append("CLIENT_UNAVAILABLE_OR_MISSING_ADMIN_CREDENTIAL")
        if not overlap:
            reasons.append("CLIENT_ACCESSES_ROUTER_ACROSS_DOCKER_NETWORK_BOUNDARY")
        if origin["scheme"] == "HTTP":
            reasons.append("ADMIN_TOKEN_CONFIGURED_OVER_PLAINTEXT_HTTP")
        if (not origin["configured_host_present"] or not origin["router_port_valid"]
                or origin["userinfo_or_query_present"]):
            reasons.append("CLIENT_ADMIN_ORIGIN_INVALID")
        consumers[name] = {
            "running": state,
            "shared_docker_network": overlap,
            "admin_token_configured": origin["admin_token_configured"],
            "origin_scheme": origin["scheme"],
            "configured_host_present": origin["configured_host_present"],
            "port_8088": origin["router_port_valid"],
            "url_userinfo_or_query_present": origin["userinfo_or_query_present"],
        }

    firewall = hook_readability if isinstance(hook_readability, dict) else {}
    firewall_readable = (
        firewall.get("ipv4_forward") is True
        and firewall.get("ipv6_forward") is True
        and firewall.get("nft_ruleset") is True
    )
    if not firewall_readable:
        reasons.append("PRIVILEGED_NETFILTER_PATH_UNVERIFIED")
    # Container address or network membership does not establish post-NAT
    # packet source identity at Docker's published host port. Never generate
    # source IP allowlists from these values alone.
    reasons.append("FORWARDED_AND_HAIRPIN_SOURCE_IDENTITY_UNVERIFIED")
    reasons.append("OWNER_CONTAINMENT_AND_ROLLBACK_APPROVAL_REQUIRED")
    return {
        "schema": "auto-router.ingress-containment-readonly-v1",
        "decision": "HOLD_OWNER_CONTROLS_REQUIRED",
        "reason_codes": sorted(set(reasons)),
        "router_running": (router.get("State") or {}).get("Running") is True,
        "router_published_wildcard_ipv4": wildcard_v4,
        "router_published_wildcard_ipv6": wildcard_v6,
        "consumers": consumers,
        "privileged_netfilter_path_readable": firewall_readable,
        "firewall_rule_safety_verified": False,
        "post_nat_source_identity_verified": False,
        "authorized_client_continuity_verified": False,
        "security_owner_approved": False,
        "credential_custody_closed": False,
        "containment_change_authorized": False,
        "production_release_authorized": False,
        "http_probes_or_service_writes_performed": False,
        "token_address_or_rule_values_reported": False,
    }


def _read_only(argv: list[str], max_bytes: int = 2_000_000) -> bytes | None:
    try:
        result = subprocess.run(argv, capture_output=True, timeout=10, check=False)
        if result.returncode == 0 and len(result.stdout) <= max_bytes:
            return result.stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def main() -> int:
    raw = _read_only(["docker", "inspect", "--type", "container", *NAMES])
    try:
        nodes = json.loads(raw) if raw is not None else None
    except (ValueError, TypeError):
        nodes = None
    # Read only; never echo or persist raw iptables/nft JSON.
    hooks = {
        "ipv4_forward": _read_only(["iptables", "-S", "FORWARD"]) is not None,
        "ipv6_forward": _read_only(["ip6tables", "-S", "FORWARD"]) is not None,
        "nft_ruleset": _read_only(["nft", "-j", "list", "ruleset"]) is not None,
    }
    print(json.dumps(summarize_containers(nodes, hooks), sort_keys=True))
    # Refuse to act as a deploy/firewall-ready green gate.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
