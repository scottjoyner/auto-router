#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from auto_router.fleet_task_dispatcher import probe_all_nodes


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe all configured LM Studio nodes")
    parser.add_argument("--provider-config", type=Path, default=REPO_ROOT / "config" / "providers.yaml")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-online", type=int, default=1)
    parser.add_argument("--require-loaded-models", type=int, default=1)
    args = parser.parse_args()

    nodes = probe_all_nodes(provider_config=args.provider_config, timeout_seconds=args.timeout)
    online = [node for node in nodes if node.online]
    loaded_count = sum(len(node.loaded_models) for node in nodes)

    if args.json:
        print(json.dumps({"nodes": [asdict(node) for node in nodes], "online_nodes": len(online), "loaded_models": loaded_count}, indent=2, sort_keys=True))
    else:
        for node in nodes:
            status = "ONLINE" if node.online else "OFFLINE"
            complete = "complete" if node.inventory_complete else "partial"
            print(f"{status:7} {node.name:36} {node.latency_ms:8.1f}ms loaded={len(node.loaded_models):2} available={len(node.all_models):2} inventory={complete}")
            if node.loaded_models:
                print("         loaded: " + ", ".join(node.loaded_models))
            if node.error:
                print("         error: " + node.error)

    if len(online) < args.require_online:
        print(f"FAIL online nodes {len(online)} < required {args.require_online}", file=sys.stderr)
        return 2
    if loaded_count < args.require_loaded_models:
        print(f"FAIL loaded models {loaded_count} < required {args.require_loaded_models}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
