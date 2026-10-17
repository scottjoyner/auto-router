#!/usr/bin/env python3
"""Non-dispatching R9700 presence report; never asserts model admission."""
import argparse
import json
import os
import platform
import socket
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROUTER = "http://100.64.43.123:8088/api/fleet/node-report"
ENDPOINT = "http://100.83.215.83:18320"


def probe(path, endpoint=ENDPOINT):
    try:
        with urlopen(endpoint + path, timeout=3) as response:
            response.read(256)
            return response.status
    except HTTPError as exc:
        return exc.code
    except (OSError, URLError):
        return 0


def read_int(path):
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return 0


def meminfo():
    d = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                d[key] = int(value.split()[0]) / 1048576
    except (OSError, ValueError, IndexError):
        pass
    return d


def publish():
    mem = meminfo()
    pci = Path("/sys/class/drm/card1/device/uevent")
    try:
        identity = "PCI_ID=1002:7551" in pci.read_text()
    except OSError:
        identity = False
    render = os.access("/dev/dri/renderD128", os.R_OK | os.W_OK)
    vram = read_int("/sys/class/drm/card1/device/mem_info_vram_total")
    health_http = probe("/health")
    auth_http = probe("/v1/models")
    toolcall_health = probe("/health", "http://100.83.215.83:18321")
    toolcall_auth = probe("/v1/models", "http://100.83.215.83:18321")
    vram_used = read_int("/sys/class/drm/card1/device/mem_info_vram_used")
    gpu_seen = identity and render and vram > 32000000000
    guarded = health_http == 200 and auth_http == 401
    payload = {
        "hostname": socket.gethostname(),
        "os": platform.system(),
        "library": [],
        "loaded": [],
        "capabilities": [],
        "specs": {
            "platform": "Linux", "hostname": socket.gethostname(),
            "cpu_cores": os.cpu_count() or 0,
            "gpu": "AMD Radeon AI PRO R9700 (gfx1201)" if gpu_seen else "",
            "gpu_backend": "Vulkan RADV",
            "gpu_device": "0000:03:00.0",
            "gpu_vram_gib": round(vram / (1024**3), 2) if gpu_seen else 0,
            "gpu_vram_used_gib": round(vram_used / (1024**3), 2),
            "system_ram_gib": round(mem.get("MemTotal", 0), 2),
            "available_ram_gib": round(mem.get("MemAvailable", 0), 2),
        },
        "health": {
            "status": "degraded",
            "gpu_canary_reachable_and_guarded": bool(gpu_seen and guarded),
            "dispatch_admitted": False,
            "dispatch_reason": "GPU verified but automatic routing not approved",
            "gpu_health_http": health_http, "gpu_models_unauth_http": auth_http,
            "toolcall_3b_health_http": toolcall_health,
            "toolcall_3b_unauth_http": toolcall_auth,
            "toolcall_3b_model_alias": "toolcall-v5-3b-combined-r2-f16",
            "toolcall_3b_unadmitted": True,
            "swap_used_gib": round(mem.get("SwapTotal", 0) - mem.get("SwapFree", 0), 2),
        },
    }
    req = Request(ROUTER, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=5) as response:
        response.read(256)
        if response.status != 200:
            raise RuntimeError(f"router HTTP {response.status}")
    print(f"r9700 heartbeat posted guarded={guarded} gpu_seen={gpu_seen} dispatch_admitted=False", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args()
    while True:
        try:
            publish()
        except Exception as exc:
            print(f"heartbeat error={type(exc).__name__}", flush=True)
        time.sleep(max(30, args.interval))
