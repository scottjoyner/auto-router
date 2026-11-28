"""Passive Joyner R9700 LFM3 DRM/receipt correlation; zero inference requests.

A positive delta during a new receipt window is *consistent with* model GPU
work but does not prove token-level attribution. Never grants deployment.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import time

SERVICE = "lfm3-joyner-inference.service"
SCHEMA = "sensorgraft.lfm3.online-probe.v1"
MODEL = "LFM3-1.7B-QAD-Q4_0"
PCI = "0000:03:00.0"


def process_identity(pid: int, proc_root: Path) -> int:
    if not 1 <= pid <= 2**31 - 1:
        raise ValueError("invalid process id")
    root = proc_root / str(pid)
    cg = (root / "cgroup").read_text()
    if not any(s.rstrip().endswith("/" + SERVICE) for s in cg.splitlines()):
        raise ValueError("unexpected service cgroup")
    text = (root / "stat").read_text()
    marker = text.rfind(") ")
    if marker < 0:
        raise ValueError("invalid proc stat")
    fields = text[marker + 2:].split()
    if len(fields) < 20:
        raise ValueError("incomplete proc stat")
    ticks = int(fields[19])
    if ticks <= 0:
        raise ValueError("invalid process start")
    return ticks


def drm_compute(pid: int, proc_root: Path) -> tuple[int, int, int]:
    fdroot = proc_root / str(pid) / "fdinfo"
    clients: dict[str, tuple[int, int, int]] = {}
    for f in fdroot.iterdir():
        if not f.name.isdigit():
            continue
        try:
            lines = f.read_text().splitlines()
        except (OSError, PermissionError):
            continue
        values = dict(line.split(":", 1) for line in lines if ":" in line)
        if values.get("drm-pdev", "").strip() != PCI:
            continue
        cid = values.get("drm-client-id", "").strip()
        value = values.get("drm-engine-compute", "").strip()
        if not cid or not cid.isdecimal() or not value.endswith(" ns"):
            raise ValueError("DRM compute counter unavailable")
        nanoseconds = int(value.removesuffix(" ns"))
        if nanoseconds < 0:
            raise ValueError("invalid DRM counter")
        vram = values.get("drm-memory-vram", "").strip()
        gtt = values.get("drm-memory-gtt", "").strip()
        if not vram.endswith(" KiB") or not gtt.endswith(" KiB"):
            raise ValueError("missing DRM memory counters")
        vals = (nanoseconds, int(vram.removesuffix(" KiB")),
                int(gtt.removesuffix(" KiB")))
        if min(vals) < 0:
            raise ValueError("negative resource counter")
        if cid in clients and clients[cid] != vals:
            raise ValueError("inconsistent duplicate DRM client")
        clients[cid] = vals
    if not clients:
        raise ValueError("no readable discrete R9700 compute client")
    return (sum(x[0] for x in clients.values()),
            sum(x[1] for x in clients.values()),
            sum(x[2] for x in clients.values()))


def receipt_time(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > 16_000_000:
        raise ValueError("receipt file missing/oversized")
    # Bounded tail read, never emits prompts, samples, response digests, or keys.
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 16384))
        tail = stream.read(16384).splitlines()
    if not tail:
        raise ValueError("empty receipts")
    obj = json.loads(tail[-1])
    if obj.get("schema") != SCHEMA or obj.get("node") != "joyner":
        raise ValueError("unexpected receipt identity")
    if obj.get("model") != MODEL or obj.get("probe_count") != 8:
        raise ValueError("unexpected model/probe budget")
    if obj.get("successful_requests") != 8:
        raise ValueError("incomplete online probe")
    timestamp = obj.get("timestamp_utc")
    if not isinstance(timestamp, str):
        raise ValueError("missing receipt timestamp")
    parsed = dt.datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        raise ValueError("naive timestamp")
    return parsed.isoformat()


def snapshot(pid: int, proc: Path, receipts: Path) -> dict:
    first = process_identity(pid, proc)
    gpu_ns, vram_kib, gtt_kib = drm_compute(pid, proc)
    stamp = receipt_time(receipts)
    if process_identity(pid, proc) != first:
        raise ValueError("PID changed during snapshot")
    return {"start_ticks": first, "compute_ns": gpu_ns,
            "vram_kib": vram_kib, "gtt_kib": gtt_kib,
            "receipt_timestamp": stamp}


def compare(a: dict, b: dict) -> dict:
    if a["start_ticks"] != b["start_ticks"]:
        raise ValueError("PID start-time mismatch")
    delta = b["compute_ns"] - a["compute_ns"]
    if delta < 0:
        raise ValueError("GPU compute counter regressed")
    start = dt.datetime.fromisoformat(a["receipt_timestamp"])
    end = dt.datetime.fromisoformat(b["receipt_timestamp"])
    if end < start:
        raise ValueError("receipt timestamp regressed")
    advanced = end > start
    observed = delta > 0 and advanced
    return {
        "schema": "lfm3.r9700.passive-gpu-observer.v1",
        "node": "joyner", "gpu": PCI, "service": SERVICE,
        "new_receipt": advanced, "compute_delta_ns": delta,
        "correlation": ("consistent_with_gpu_work_during_new_probe_window"
                        if observed else "inconclusive"),
        "proof_of_per_token_attribution": False,
        "inference_requests_made": 0,
        "production_ready": False, "release_gate": "HOLD",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pid", required=True, type=int)
    ap.add_argument("--receipts", required=True, type=Path)
    ap.add_argument("--wait-seconds", type=int, default=45)
    args = ap.parse_args()
    if not 0 <= args.wait_seconds <= 60:
        ap.error("wait-seconds must be 0..60")
    try:
        a = snapshot(args.pid, Path("/proc"), args.receipts)
        time.sleep(args.wait_seconds)
        b = snapshot(args.pid, Path("/proc"), args.receipts)
        result = compare(a, b)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        ap.exit(78, "HOLD: passive evidence unavailable: " + str(exc) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
