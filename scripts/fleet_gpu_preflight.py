#!/usr/bin/env python3
"""Read-only GPU endpoint / fleet telemetry checks; never authenticates or dispatches."""
from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINTS = (
    ("xwing", "minicpm5-1b", "http://100.108.99.47:18302", "ROCm0", "gfx1151"),
    ("xwing", "bonsai2-27b-ptq1-vulkan", "http://100.108.99.47:18271", "Vulkan0", "gfx1151"),
    ("joyner", "minicpm5-1b", "http://100.83.215.83:18320", "Vulkan1", "gfx1201"),
    ("joyner", "toolcall-v5-3b", "http://100.83.215.83:18321", "Vulkan1", "gfx1201"),
)
ROUTER = "http://127.0.0.1:8088"
EXPECTED_UNAUTHENTICATED_MODELS_HTTP = 401
MAX_REPORT_AGE_S = 180
MAX_XWING_LOAD_PER_CPU = 1.5
MAX_SWAP_GIB = 24.0


def fetch_status(url: str, *, timeout_s: float) -> tuple[int | None, str | None]:
    try:
        with urlopen(Request(url, headers={"User-Agent": "fleet-gpu-preflight/1.0"}), timeout=timeout_s) as r:
            r.read(1024)
            return r.status, None
    except HTTPError as exc:
        return exc.code, None
    except (URLError, TimeoutError, OSError) as exc:
        return None, type(exc).__name__


def fetch_report(host: str, *, timeout_s: float) -> dict:
    try:
        with urlopen(f"{ROUTER}/api/fleet/node/{host}", timeout=timeout_s) as r:
            obj = json.load(r)
            return obj if isinstance(obj, dict) else {}
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return {}


def preflight(timeout_s: float = 4.0) -> dict:
    nodes = []
    for host, service, base, device, gfx in ENDPOINTS:
        health_http, health_error = fetch_status(base + "/health", timeout_s=timeout_s)
        models_http, models_error = fetch_status(base + "/v1/models", timeout_s=timeout_s)
        report = fetch_report(host, timeout_s=timeout_s)
        health = report.get("health") or {}
        specs = report.get("specs") or {}
        age = max(0, int(time.time()) - int(report.get("received_at") or 0)) if "received_at" in report else None
        warnings = []
        if health_http != 200:
            warnings.append("gpu_endpoint_health_failed")
        if models_http != EXPECTED_UNAUTHENTICATED_MODELS_HTTP:
            warnings.append("authentication_boundary_failed_or_unknown")
        if age is None:
            warnings.append("fleet_report_missing")
        elif age > MAX_REPORT_AGE_S:
            warnings.append("fleet_report_stale")
        if health and health.get("status") != "healthy":
            warnings.append("fleet_report_unhealthy")
        if host == "xwing":
            load = float(health.get("load_1m") or 0)
            cores = int(specs.get("cpu_cores") or 1)
            swap = float(health.get("swap_used_gib") or 0)
            if load / cores > MAX_XWING_LOAD_PER_CPU:
                warnings.append("excessive_cpu_load")
            if swap > MAX_SWAP_GIB:
                warnings.append("swap_pressure")
        nodes.append({
            "node": host,
            "service": service,
            "target_device": device,
            "target_arch": gfx,
            "endpoint": base,
            "health_http": health_http,
            "models_without_key_http": models_http,
            "health_error": health_error,
            "models_error": models_error,
            "fleet_report_age_s": age,
            "fleet_health": health.get("status", "unknown"),
            "fleet_load_1m": health.get("load_1m"),
            "fleet_swap_used_gib": health.get("swap_used_gib"),
            "fleet_gpu_vram_used_gib": specs.get("gpu_vram_used_gib"),
            "api_reachable_and_guarded": health_http == 200 and models_http == EXPECTED_UNAUTHENTICATED_MODELS_HTTP,
            "approved_for_auto_dispatch": False,  # never infer admission from HTTP health
            "warnings": warnings,
        })
    return {"timestamp_unix": int(time.time()), "check": "read_only", "nodes": nodes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=4.0)
    parser.add_argument("--require-healthy", action="store_true")
    args = parser.parse_args()
    result = preflight(timeout_s=args.timeout)
    print(json.dumps(result, indent=2))
    if args.require_healthy and any(n["warnings"] for n in result["nodes"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
