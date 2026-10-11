#!/usr/bin/env python3
"""Issue #37: bounded, read-only residency preflight across provider services.

This standalone diagnostic deliberately does not modify the existing discovery
validator, router registry, or route admissions. Duplicate physical node IDs are
allowed because separate services can run on different ports of one node.
Only GET-based provider inventory probes are permitted when --probe is explicit.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml

from auto_router.fleet_task_dispatcher import (
    DiscoveryConfigError,
    _configured_inventory,
    _expand_env,
    _probe_provider,
    _validate_base_url,
)

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "providers.yaml"


def configured_services(path: Path) -> list[dict[str, Any]]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DiscoveryConfigError("cannot read provider registry") from exc
    if not isinstance(document, dict) or not isinstance(document.get("providers"), list):
        raise DiscoveryConfigError("provider registry must contain providers list")
    names: set[str] = set()
    services = []
    for row in document["providers"]:
        if not isinstance(row, dict):
            raise DiscoveryConfigError("provider record must be a mapping")
        if str(row.get("type") or "").lower() != "lmstudio":
            continue
        name = str(row.get("name") or "").strip()
        node_id = str(row.get("node_id") or name).strip()
        raw_url = str(row.get("base_url") or "")
        base_url = _expand_env(raw_url).strip()
        if not name or not node_id or not base_url:
            raise DiscoveryConfigError("LM Studio entry missing service, node or URL")
        if name in names:
            raise DiscoveryConfigError("duplicate provider service name")
        # Retain the production offline-host validation. Do not allow public URLs.
        _validate_base_url(base_url, provider_name=name)
        configured, aliases = _configured_inventory(row)
        names.add(name)
        services.append({
            "name": name,
            "node_id": node_id,
            "base_url": base_url,
            "configured_models": configured,
            "model_aliases": aliases,
            "api_token_env": row.get("api_token_env", "LMSTUDIO_API_TOKEN"),
        })
    return services


def summarize(
    services: list[dict[str, Any]],
    *,
    probe: bool = False,
    timeout_seconds: float = 1.0,
    max_workers: int = 2,
) -> dict[str, Any]:
    if not 0.1 <= timeout_seconds <= 10:
        raise ValueError("timeout must be between 0.1 and 10 seconds")
    if not 1 <= max_workers <= 4:
        raise ValueError("workers must be between 1 and 4")
    services_by_node: dict[str, list[str]] = defaultdict(list)
    for service in services:
        services_by_node[service["node_id"]].append(service["name"])

    results = []
    if not probe:
        for service in services:
            results.append({
                "provider_service": service["name"],
                "physical_node_id": service["node_id"],
                "inventory_state": "not_probed",
                "resident_models": [],
                "resident_model_count": None,
                "inventory_authoritative": False,
                "canary_candidate": False,
            })
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            probes = {
                executor.submit(
                    _probe_provider, service, timeout_seconds=timeout_seconds
                ): service
                for service in services
            }
            for task in as_completed(probes):
                service = probes[task]
                # Fail closed on an unexpected probe exception; don't misreport
                # a configured model or cached data as a resident instance.
                try:
                    info = task.result()
                except Exception:
                    results.append({
                        "provider_service": service["name"],
                        "physical_node_id": service["node_id"],
                        "inventory_state": "probe_error",
                        "resident_models": [],
                        "resident_model_count": None,
                        "inventory_authoritative": False,
                        "canary_candidate": False,
                    })
                    continue
                results.append({
                    "provider_service": service["name"],
                    "physical_node_id": service["node_id"],
                    "inventory_state": (
                        "authoritative" if info.inventory_authoritative
                        else "non_authoritative" if info.online else "offline"
                    ),
                    "observed_model_count": len(info.all_models),
                    "resident_models": (
                        sorted(info.loaded_models) if info.inventory_authoritative else []
                    ),
                    "resident_model_count": (
                        len(info.loaded_models) if info.inventory_authoritative else None
                    ),
                    "inventory_authoritative": info.inventory_authoritative,
                    "canary_candidate": bool(
                        info.online
                        and info.inventory_authoritative
                        and info.loaded_models
                    ),
                })
    results.sort(key=lambda row: (row["physical_node_id"], row["provider_service"]))
    duplicate_nodes = {
        node: sorted(names) for node, names in sorted(services_by_node.items())
        if len(names) > 1
    }
    counts = Counter(result["inventory_state"] for result in results)
    return {
        "schema": "p0-37-read-only-residency-v1",
        "network_probed": probe,
        "provider_services": len(services),
        "physical_nodes": len(services_by_node),
        "multi_service_nodes": duplicate_nodes,
        "inventory_states": dict(sorted(counts.items())),
        "canary_candidate_services": sum(row["canary_candidate"] for row in results),
        "provider_observer_attestation": False,
        "two_user_execution_proven": False,
        "services": results,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only P0 #37 service-by-service residency diagnostic"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--probe", action="store_true",
        help="Explicitly permit bounded GET requests to configured private providers",
    )
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--max-services", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.max_services <= 32:
            raise ValueError("max services must be between 1 and 32")
        services = configured_services(args.config)
        if len(services) > args.max_services:
            raise DiscoveryConfigError("provider count exceeds explicit probe budget")
        result = summarize(
            services, probe=args.probe,
            timeout_seconds=args.timeout, max_workers=args.max_workers,
        )
    except (DiscoveryConfigError, ValueError) as exc:
        print("PRECHECK_FAIL", str(exc))
        return 4
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        print(
            f"STATE {'GET_PROBED' if args.probe else 'CONFIG_ONLY'}"
            f" services={result['provider_services']}"
            f" physical_nodes={result['physical_nodes']}"
            f" canary_candidate_services={result['canary_candidate_services']}"
        )
        for item in result["services"]:
            count = item["resident_model_count"]
            count_text = "UNKNOWN" if count is None else str(count)
            print(
                f"{item['physical_node_id']}/{item['provider_service']}"
                f" inventory={item['inventory_state']}"
                f" resident={count_text}"
                f" candidate={str(item['canary_candidate']).lower()}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
