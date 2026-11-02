#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
# Use the script's checked-out source even when a different editable
# Auto-Router installation has injected its own src directory into sys.path.
# Checking only for membership is insufficient when it appears *after* that
# editable location; the CLI would then probe using the wrong implementation.
if str(SRC_ROOT) in sys.path:
    sys.path.remove(str(SRC_ROOT))
sys.path.insert(0, str(SRC_ROOT))

from auto_router.fleet_task_dispatcher import DiscoveryConfigError, probe_all_nodes


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe all configured LM Studio nodes")
    parser.add_argument(
        "--provider-config",
        type=Path,
        default=REPO_ROOT / "config" / "providers.yaml",
    )
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-online", type=int, default=1)
    parser.add_argument("--require-loaded-models", type=int, default=1)
    parser.add_argument("--require-authoritative", type=int, default=0)
    args = parser.parse_args()

    try:
        nodes = probe_all_nodes(
            provider_config=args.provider_config,
            timeout_seconds=args.timeout,
            max_workers=args.max_workers,
        )
    except (DiscoveryConfigError, ValueError) as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "status": "configuration-error",
                        "error": str(exc),
                        "nodes": [],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return 4

    online = [node for node in nodes if node.online]
    authoritative = [node for node in nodes if node.inventory_authoritative]
    degraded = [
        node
        for node in nodes
        if node.online and not node.inventory_authoritative
    ]
    loaded_count = sum(len(node.loaded_models) for node in nodes)
    status = "healthy"
    if not nodes or not online:
        status = "offline"
    elif degraded or len(online) != len(nodes):
        status = "degraded"

    # The `nodes` array is one row per configured provider lane. Do not
    # mistake multiple runtimes on xwing/destroyer for multiple computers.
    physical_node_count = len({node.name for node in nodes})
    online_physical_nodes = len({node.name for node in online})
    authoritative_physical_nodes = len({node.name for node in authoritative})
    summary = {
        "status": status,
        "nodes": [asdict(node) for node in nodes],
        "node_count": len(nodes),  # Backward-compatible provider-lane count.
        "physical_node_count": physical_node_count,
        "online_physical_nodes": online_physical_nodes,
        "authoritative_physical_nodes": authoritative_physical_nodes,
        "online_nodes": len(online),  # Legacy: this is online lanes.
        "authoritative_nodes": len(authoritative),
        "degraded_nodes": len(degraded),
        "loaded_models": loaded_count,
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        for node in nodes:
            state = "ONLINE" if node.online else "OFFLINE"
            authority = "authoritative" if node.inventory_authoritative else "non-authoritative"
            print(
                f"{state:7} {node.name:28} lane={node.provider_name:30} "
                f"{node.latency_ms:8.1f}ms "
                f"loaded={len(node.loaded_models):2} observed={len(node.all_models):2} "
                f"inventory={authority} source={node.discovery_source}"
            )
            if node.loaded_models:
                print("         loaded: " + ", ".join(node.loaded_models))
            for warning in node.warnings:
                print("         warning: " + warning)
            if node.error:
                print("         error: " + node.error)
        print(
            f"STATUS {status} online_lanes={len(online)}/{len(nodes)} "
            f"physical_nodes={physical_node_count} online_physical={online_physical_nodes} "
            f"authoritative_lanes={len(authoritative)} loaded={loaded_count}"
        )

    if len(online) < args.require_online:
        print(
            f"FAIL online nodes {len(online)} < required {args.require_online}",
            file=sys.stderr,
        )
        return 2
    if loaded_count < args.require_loaded_models:
        print(
            f"FAIL loaded models {loaded_count} < required {args.require_loaded_models}",
            file=sys.stderr,
        )
        return 3
    if len(authoritative) < args.require_authoritative:
        print(
            "FAIL authoritative nodes "
            f"{len(authoritative)} < required {args.require_authoritative}",
            file=sys.stderr,
        )
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
