#!/usr/bin/env python3
"""Fleet node reporter.

Phones the auto-router's pubsub fleet endpoint every --interval seconds with this
node's LM Studio library (the full downloaded catalog), its currently-loaded
models, and *real* machine specs. The orchestrator uses the published specs to
build per-node RAM budgets, so they must come from the node itself -- not from a
benchmark run stamped by whatever host executed bench_fleet.py.

Usage:
  python3 fleet_node_reporter.py --router-url http://100.64.43.123:8088 --interval 30
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


LM_PORT = 1234


# --------------------------------------------------------------------------- #
# Specs (real hardware on the node running this reporter)
# --------------------------------------------------------------------------- #
def _read_proc_meminfo() -> Dict[str, float]:
    """Parse /proc/meminfo into gibibyte values for the keys we care about."""
    out: Dict[str, float] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split(":")
                if len(parts) != 2:
                    continue
                key = parts[0].strip()
                val = parts[1].strip().split()
                if len(val) < 2 or val[1] != "kB":
                    continue
                try:
                    out[key] = int(val[0]) / (1024 * 1024)
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _linux_cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as fh:
            seen = False
            for line in fh:
                if line.lower().startswith("model name"):
                    val = line.split(":", 1)[1].strip()
                    if val:
                        return val
                if line.lower().startswith("processor") and not seen:
                    seen = True
    except OSError:
        pass
    return ""


def _linux_gpu() -> str:
    """Best-effort GPU detection on Linux. Returns a short label or ''."""
    # nvidia-smi is the most reliable signal of a usable accelerator.
    if shutil.which("nvidia-smi"):
        try:
            txt = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=10,
            ).strip()
            names = [n.strip() for n in txt.splitlines() if n.strip()]
            if names:
                return ", ".join(sorted(set(names)))
        except (subprocess.SubprocessError, OSError):
            pass
    # Fall back to scanning lspci for known GPU vendors.
    if shutil.which("lspci"):
        try:
            txt = subprocess.check_output(
                ["lspci"], text=True, stderr=subprocess.DEVNULL, timeout=10
            )
            hits = []
            for line in txt.splitlines():
                low = line.lower()
                if any(kind in low for kind in ("vga compatible controller", "3d controller", "display controller")):
                    if "nvidia" in low:
                        hits.append("NVIDIA")
                    elif "amd" in low or "advanced micro devices" in low:
                        hits.append("AMD")
                    elif "intel" in low:
                        hits.append("Intel")
            if hits:
                return ", ".join(sorted(set(hits)))
        except (subprocess.SubprocessError, OSError):
            pass
    return ""


def _macos_memory() -> Dict[str, float]:
    """Return {MemTotal_gib, MemAvailable_gib} on macOS using sysctl + vm_stat."""
    out: Dict[str, float] = {}
    try:
        mem_bytes = int(
            subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True, stderr=subprocess.DEVNULL
            ).strip()
        )
        out["MemTotal_gib"] = mem_bytes / (1024**3)
    except (subprocess.SubprocessError, OSError, ValueError):
        return out
    # vm_stat reports page counts; default page size 4096 on Apple silicon/Intel.
    try:
        txt = subprocess.check_output(
            ["vm_stat"], text=True, stderr=subprocess.DEVNULL, timeout=10
        )
        pages = 4096
        free_pages = 0
        inactive_pages = 0
        for line in txt.splitlines():
            if line.startswith("Pages free:"):
                free_pages = int(line.split(":")[1].strip().replace(".", ""))
            elif line.startswith("Pages inactive:"):
                inactive_pages = int(line.split(":")[1].strip().replace(".", ""))
        # Treat free + inactive as reclaimable available memory (conservative).
        out["MemAvailable_gib"] = (free_pages + inactive_pages) * pages / (1024**3)
    except (subprocess.SubprocessError, OSError, ValueError):
        pass
    return out


def _macos_cpu_model() -> str:
    try:
        return subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def _macos_gpu() -> str:
    if shutil.which("system_profiler"):
        try:
            txt = subprocess.check_output(
                ["system_profiler", "SPDisplaysDataType"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            for line in txt.splitlines():
                s = line.strip()
                if s.startswith("Chipset Model:"):
                    return s.split(":", 1)[1].strip()
        except (subprocess.SubprocessError, OSError):
            pass
    return "Apple"  # macOS without discrete GPU is almost always Apple silicon/IGPU


def _specs() -> Dict[str, Any]:
    """Collect real, node-local hardware specs. Never falls back to benchmark
    artifacts -- if we cannot read a value we leave it blank rather than lie."""
    specs: Dict[str, Any] = {
        "platform": platform.system(),
        "hostname": socket.gethostname(),
        "cpu_model": "",
        "cpu_cores": os.cpu_count() or 0,
        "gpu": "",
        "system_ram_gib": 0.0,
        "available_ram_gib": 0.0,
    }
    if platform.system() == "Linux":
        mem = _read_proc_meminfo()
        specs["system_ram_gib"] = round(mem.get("MemTotal", 0.0), 2)
        specs["available_ram_gib"] = round(mem.get("MemAvailable", 0.0), 2)
        specs["cpu_model"] = _linux_cpu_model()
        specs["gpu"] = _linux_gpu()
    elif platform.system() == "Darwin":
        mem = _macos_memory()
        specs["system_ram_gib"] = round(mem.get("MemTotal_gib", 0.0), 2)
        specs["available_ram_gib"] = round(
            mem.get("MemAvailable_gib", mem.get("MemTotal_gib", 0.0)), 2
        )
        specs["cpu_model"] = _macos_cpu_model()
        specs["gpu"] = _macos_gpu()
    else:
        # Unknown platform: report what little we can, no fabrication.
        specs["cpu_model"] = platform.processor()
    return specs


# --------------------------------------------------------------------------- #
# LM Studio state
# --------------------------------------------------------------------------- #
def _http_get_json(url: str, timeout: float = 5.0) -> Optional[Any]:
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - reporter must never crash the loop
        return None


def _loaded_models(lm_url: str) -> List[str]:
    """Currently-loaded model identifiers via the LM Studio native API.

    Use /api/v1/models and filter to items where loaded_instances is non-empty
    (actually resident in VRAM/RAM). The top-level 'loaded' boolean is unreliable.
    """
    data = _http_get_json(f"{lm_url.rstrip('/')}/api/v1/models")
    if not isinstance(data, dict):
        return []
    items = data.get("models") or []
    out = []
    for m in items:
        if not isinstance(m, dict):
            continue
        # LM Studio /api/v1/models uses 'key' field (not 'id')
        mid = m.get("key") or m.get("model_key") or m.get("path") or m.get("id")
        if not mid:
            continue
        # Only include models that are actually resident (loaded_instances non-empty)
        loaded_instances = m.get("loaded_instances") or []
        if isinstance(loaded_instances, list) and len(loaded_instances) > 0:
            out.append(mid)
    return out


def _library(lm_url: str) -> List[Dict[str, Any]]:
    """Full downloaded catalog (loaded or not) via the LM Studio native API."""
    data = _http_get_json(f"{lm_url.rstrip('/')}/api/v0/models")
    if not isinstance(data, dict):
        return []
    items = data.get("data") or []
    out = []
    for m in items:
        if not isinstance(m, dict):
            continue
        mid = m.get("id") or m.get("model_key") or m.get("path")
        if mid:
            out.append({"id": str(mid), "path": m.get("path", "")})
    return out


# --------------------------------------------------------------------------- #
# Report + loop
# --------------------------------------------------------------------------- #
def _power_profile() -> str:
    """Fleet standard loadout tier (see knowledge FLEET-STANDARD-LOADOUTS.md).

    Prefers the node switcher's state file (/run/fleet-power-profile.current,
    written by fleet-power-profile); falls back to battery status so un-wired
    laptop nodes still report CONSERVE while discharging."""
    try:
        with open("/run/fleet-power-profile.current", "r", encoding="utf-8") as fh:
            val = fh.read().strip()
            if val:
                return val
    except OSError:
        pass
    try:
        import glob

        for path in glob.glob("/sys/class/power_supply/BAT*/status"):
            with open(path, "r", encoding="utf-8") as fh:
                if "Discharging" in fh.read():
                    return "conserve"
    except OSError:
        pass
    return "daily"


def _power_model_class(default: str = "full") -> str:
    """Class of model served under the current power profile.

    From MODEL_CLASS in the profile env file (full | essential). Nodes without
    a switcher report "full" — the daily loadout assumption."""
    try:
        with open("/home/scott/.config/fleet/power-profile.env", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MODEL_CLASS="):
                    val = line.split("=", 1)[1].strip()
                    if val:
                        return val
    except OSError:
        pass
    return default


def _capabilities(lm_url: str) -> List[str]:
    """Node capabilities, mirroring the agent's detection minus the generic
    "script" capability (run_fleet_node_agent.py deliberately blanks that on
    production workers). Detected per cycle so the fleet does not advertise a
    frozen view from whenever the process happened to start."""
    caps = set()
    data = _http_get_json(f"{lm_url.rstrip('/')}/v1/models")
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        caps.update({"llm", "lmstudio"})
    for command in ("ffmpeg", "python3"):
        if shutil.which(command):
            caps.add(command)
    return sorted(caps)


def _disk() -> Dict[str, Any]:
    """Real local disk usage for the fleet root filesystem."""
    try:
        st = os.statvfs("/")
    except OSError:
        return {}
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - (st.f_bfree * st.f_frsize)
    return {
        "root_total_gib": round(total / (1024**3), 2),
        "root_used_gib": round(used / (1024**3), 2),
        "root_avail_gib": round(free / (1024**3), 2),
        "root_use_pct": round((used / total) * 100, 1) if total else 0.0,
    }



def _gpu_canary_status() -> Dict[str, Any]:
    """Local Tailnet-only GPU health evidence; not proof of routing admission."""
    root = "http://100.108.99.47:18302"
    result: Dict[str, Any] = {
        "gpu_canary_health_http": 0,
        "gpu_canary_anonymous_models_http": 0,
        "gpu_canary_reachable_and_guarded": False,
        "gpu_bonsai_canary_health_http": 0,
        "gpu_bonsai_anonymous_models_http": 0,
        "gpu_bonsai_reachable_and_guarded": False,
        "gpu_dispatch_admitted": False,
    }
    for url, field in ((root + "/health", "gpu_canary_health_http"),
                       (root + "/v1/models", "gpu_canary_anonymous_models_http"),
                       ("http://100.108.99.47:18271/health", "gpu_bonsai_canary_health_http"),
                       ("http://100.108.99.47:18271/v1/models", "gpu_bonsai_anonymous_models_http")):
        try:
            with urlopen(Request(url), timeout=2.0) as response:
                response.read(128)
                result[field] = response.status
        except HTTPError as exc:
            result[field] = exc.code
        except (URLError, OSError, TimeoutError):
            pass
    result["gpu_canary_reachable_and_guarded"] = (
        result["gpu_canary_health_http"] == 200
        and result["gpu_canary_anonymous_models_http"] == 401
    )
    result["gpu_bonsai_reachable_and_guarded"] = (
        result["gpu_bonsai_canary_health_http"] == 200
        and result["gpu_bonsai_anonymous_models_http"] == 401
    )
    return result


def _health(library: List[Dict[str, Any]], loaded: List[str]) -> Dict[str, Any]:
    """Node-local health. The router reads this from a nested "health" key, not
    from top-level "healthy"/"reported_at", so it must stay nested."""
    mem = _read_proc_meminfo()
    gpu = _gpu_canary_status()
    api_up = bool(library) or bool(loaded)
    if api_up and loaded:
        status = "healthy"
    elif api_up or gpu["gpu_canary_reachable_and_guarded"]:
        status = "degraded"
    else:
        status = "unhealthy"
    swap_total = mem.get("SwapTotal", 0.0)
    swap_free = mem.get("SwapFree", 0.0)
    return {
        "status": status,
        **gpu,
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "lmstudio_api": "up" if api_up else "down",
        "library_models": len(library),
        "loaded_models": len(loaded),
        "available_ram_gib": round(mem.get("MemAvailable", 0.0), 2),
        "swap_used_gib": round(max(0.0, swap_total - swap_free), 2),
        "load_1m": os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0,
    }


def build_report(lm_url: str) -> Dict[str, Any]:
    specs = _specs()
    hostname = socket.gethostname()
    library = _library(lm_url)
    loaded = _loaded_models(lm_url)
    return {
        "hostname": hostname,
        "host_name": hostname,
        "library": library,
        "loaded": loaded,
        "capabilities": _capabilities(lm_url),
        "specs": specs,
        "disk": _disk(),
        "health": _health(library, loaded),
        "power_profile": _power_profile(),
        "power_model_class": _power_model_class(),
    }


def post_report(router_url: str, report: Dict[str, Any], timeout: float = 10.0) -> bool:
    url = f"{router_url.rstrip('/')}/api/fleet/node-report"
    data = json.dumps(report).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (URLError, HTTPError, OSError) as exc:
        print(f"post_report failed: {exc}", file=sys.stderr)
        return False


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fleet node reporter")
    parser.add_argument("--router-url", default="http://100.64.43.123:8088")
    parser.add_argument("--lmstudio-url", default=f"http://localhost:{LM_PORT}")
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args(argv)

    print(
        f"reporter: router={args.router_url} lmstudio={args.lmstudio_url} "
        f"interval={args.interval}s",
        flush=True,
    )
    while True:
        report = build_report(args.lmstudio_url)
        ok = post_report(args.router_url, report)
        print(
            f"report {report['hostname']}: library={len(report['library'])} "
            f"loaded={len(report['loaded'])} ram={report['specs'].get('system_ram_gib')} "
            f"avail={report['specs'].get('available_ram_gib')} posted={ok}",
            flush=True,
        )
        time.sleep(max(1.0, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
