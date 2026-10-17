#!/usr/bin/env python3
"""Router-container read-only reachability/auth-boundary check; no keys or dispatch."""
from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

# This file is intentionally self-contained: pipe it to python3 in the router
# container. No secret mounts, repo source copy, config reload, or API POST.
GPU_ENDPOINTS = (
    ("joyner", "toolcall-3b", "http://100.83.215.83:18321"),
    ("joyner", "minicpm-1b", "http://100.83.215.83:18320"),
    ("xwing", "ternary-bonsai-27b", "http://100.108.99.47:18271"),
    ("xwing", "minicpm-1b", "http://100.108.99.47:18302"),
)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_http_code(url: str, timeout: float = 3.0) -> int | None:
    try:
        request = Request(url, headers={"User-Agent": "fleet-gpu-router-network-probe/1.0"})
        with build_opener(NoRedirects).open(request, timeout=timeout) as response:
            response.read(128)
            return int(response.status)
    except HTTPError as err:
        return err.code
    except (URLError, TimeoutError, OSError):
        return None


def probe(timeout: float = 3.0) -> dict:
    rows = []
    for node, model, root in GPU_ENDPOINTS:
        start = time.monotonic()
        health = get_http_code(root + "/health", timeout)
        anon_models = get_http_code(root + "/v1/models", timeout)
        rows.append({
            "node": node,
            "model": model,
            "listener": root,
            "health_http": health,
            "anonymous_models_http": anon_models,
            "network_ready_and_auth_guarded": health == 200 and anon_models == 401,
            "approved_for_auto_dispatch": False,
            "elapsed_s": round(time.monotonic() - start, 3),
        })
    return {
        "check": "read_only_no_secrets",
        "ready_listeners": sum(1 for x in rows if x["network_ready_and_auth_guarded"]),
        "total_listeners": len(rows),
        "automatic_dispatch": "disabled",
        "endpoints": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--require-healthy", action="store_true")
    args = parser.parse_args()
    if not (0 < args.timeout <= 10):
        parser.error("timeout must be >0 and <=10 seconds")
    result = probe(args.timeout)
    print(json.dumps(result, indent=2))
    return 2 if args.require_healthy and result["ready_listeners"] != result["total_listeners"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
