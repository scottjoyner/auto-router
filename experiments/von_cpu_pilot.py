#!/usr/bin/env python3
"""Explicit, bounded Von 1.3 CPU research pilot. Never modifies auto-router.

Modes: default read-only preflight; --prefetch pinned HF snapshot; --execute
loads only offline, checks hashes, and evaluates <=4 curated fictional cases.
Always call --execute inside an external CPU/memory/time-constrained scope.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from hashlib import sha256
from pathlib import Path

from typed_decision_shadow import score
from von_choice_shadow import decode_response, fixture, observation

HF_REPO = "wfzyx/von"
HF_REV = "498ceba33390b32cfefaab6422ec380318ba9b99"
WEIGHTS = {
    "model.safetensors": "af57d5d2ab15715a753a1eb4add4271d1aecce7e76f3365f629c082df329297a",
    "option_marker.pt": "3faf27f88d30aaf9aa37860d4cdef99f1d05450cf40364f6236ac892d4d139ed",
}
REQUIRED = (
    "model.safetensors", "option_marker.pt", "marker_calibration.json",
    "config.json", "tokenizer.json", "tokenizer_config.json",
)
MIN_RAM_GIB = 11.0
MIN_FREE_DISK_GIB = 70.0
MAX_CACHE_BYTES = 4_500_000_000
FIXTURE_SHA = "b52683ae6b3205e71871c964e619910974e8a5016d0ed0fb0d256400dbe50742"


def file_sha(path: Path) -> str:
    h = sha256()
    with path.open("rb") as file:
        for piece in iter(lambda: file.read(2 << 20), b""):
            h.update(piece)
    return h.hexdigest()


def admissible_workspace(path: Path) -> Path:
    root = path.resolve(strict=False)
    if not root.is_relative_to(Path("/tmp")) or root == Path("/tmp"):
        raise ValueError("workspace must reside under /tmp")
    if path.is_symlink():
        raise ValueError("workspace symlink not permitted")
    return root


def physical_preflight(*, require_capacity: bool = True) -> dict:
    fields = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        if ":" in line:
            name, rest = line.split(":", 1)
            fields[name] = int(rest.split()[0])
    available = fields["MemAvailable"] / 1048576
    disk = os.statvfs("/tmp")
    free_disk = disk.f_bavail * disk.f_frsize / (1024 ** 3)
    cpus = os.cpu_count() or 1
    load = os.getloadavg()[0]
    power_root = Path("/sys/class/power_supply")
    devices = list(power_root.glob("*")) if power_root.exists() else []
    batteries = [p for p in devices if (p / "type").exists() and
                 (p / "type").read_text().strip() == "Battery"]
    supplies = [p for p in devices if (p / "type").exists() and
                (p / "type").read_text().strip() == "Mains"]
    has_ac = any((p / "online").exists() and (p / "online").read_text().strip() == "1"
                 for p in supplies)
    ac_ok = not batteries or has_ac
    result = {
        "cpu_count": cpus, "load1": round(load, 2),
        "available_ram_gib": round(available, 2),
        "free_tmp_disk_gib": round(free_disk, 2),
        "ac_power": ac_ok,
        "capacity_admitted": (
            available >= MIN_RAM_GIB and free_disk >= MIN_FREE_DISK_GIB and
            load < max(1, cpus * 0.55) and ac_ok
        ),
        "minimum_ram_gib": MIN_RAM_GIB, "minimum_disk_gib": MIN_FREE_DISK_GIB,
        "hf_commit": HF_REV,
        "safe_for_production": False,
    }
    if require_capacity and not result["capacity_admitted"]:
        raise ValueError("CPU pilot rejected by dynamic RAM/disk/load/AC admission")
    return result


def check_snapshot(snapshot: Path) -> dict:
    if not snapshot.is_dir():
        raise ValueError("pinned model snapshot absent")
    for name in REQUIRED:
        if not (snapshot / name).is_file():
            raise ValueError("missing checkpoint component: " + name)
    for name, expected in WEIGHTS.items():
        if file_sha(snapshot / name) != expected:
            raise ValueError("pinned checkpoint hash mismatch: " + name)
    return {name: file_sha(snapshot / name) for name in REQUIRED}


def prefetch(work: Path) -> dict:
    physical_preflight()
    if not work.is_dir() or (work / "hf-cache").exists():
        raise ValueError("prefetch requires a new model cache under /tmp")
    from huggingface_hub import snapshot_download

    snapshot = Path(snapshot_download(
        repo_id=HF_REPO, revision=HF_REV, cache_dir=str(work / "hf-cache"),
        allow_patterns=list(REQUIRED), max_workers=2,
    ))
    hashes = check_snapshot(snapshot)
    # HF snapshots symlink to blobs; count disk blocks only once.
    cache_bytes = sum(p.stat().st_size for p in (work / "hf-cache").rglob("*")
                      if p.is_file() and not p.is_symlink())
    if cache_bytes > MAX_CACHE_BYTES:
        raise ValueError("download cache exceeds 4.5 GB evidence budget")
    return {
        "snapshot": str(snapshot), "hf_commit": HF_REV,
        "checkpoint_sha256": hashes, "cache_bytes": cache_bytes,
        "actual_inference": False,
    }


def run(work: Path, cases: Path, out: Path, *, max_requests: int) -> dict:
    physical_preflight()
    if not 1 <= max_requests <= 4:
        raise ValueError("pilot can evaluate at most four examples")
    rows, fixture_digest = fixture(cases)
    if fixture_digest != FIXTURE_SHA:
        raise ValueError("only exact pinned fictional four-case fixture accepted")
    if len(rows) > max_requests:
        raise ValueError("request count exceeds explicit hard pilot limit")
    if out.exists() or not out.resolve(strict=False).is_relative_to(work):
        raise ValueError("new result must be inside admitted /tmp workspace")
    snapshot = work / "hf-cache" / "models--wfzyx--von" / "snapshots" / HF_REV
    hashes = check_snapshot(snapshot)
    # Offline during inference, no possibility of downloading on a cold cache.
    os.environ.update({
        "VON_CHECKPOINT_DIR": str(snapshot),
        "VON_MODEL_ID": HF_REPO,
        "VON_CHAINS_DIR": "off", "VON_ON_OVERFLOW": "refuse",
        "VON_DEVICE": "cpu", "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": "",
        "HIP_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "2",
        "MKL_NUM_THREADS": "2", "TOKENIZERS_PARALLELISM": "false",
    })
    from von.engine import VonEngine

    engine = VonEngine.get_instance(device="cpu")
    observations = []
    for case in rows:
        started = time.perf_counter()
        answer = engine.evaluate(
            state=case["state"],
            questions={"shadow_0": {
                "type": "choice",
                "instructions": case["instructions"],
                "criteria": case["criteria"],
            }},
            model="von-1.3.0",
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        body = json.dumps(answer.model_dump()).encode("utf-8")
        choice, confidence = decode_response(body, {}, case, "von-1.3.0")
        observations.append(observation(
            case, model_id="von", revision="von-1.3.0",
            status="ok", choice=choice, confidence=confidence,
            duration=elapsed_ms,
        ))
    result = score(observations, baseline="von")
    brief = {
        "model_id": "von-1.3.0", "hf_commit": HF_REV,
        "checkpoint_sha256": hashes,
        "case_fixture_sha256": fixture_digest,
        "total_observations": len(observations),
        "results": result["models"]["von"],
        "host_class": "dedicated_cpu_pilot",
        "execution_attested": False,
        "independent_labels": False,
        "safe_for_production": False,
        "no_fleet_routing_changes": True,
        "measurement_includes_python_encode_and_forward": True,
    }
    content = (json.dumps(brief, indent=2, sort_keys=True) + "\n").encode()
    with os.fdopen(os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                          getattr(os, "O_NOFOLLOW", 0), 0o600), "wb") as file:
        file.write(content)
    return brief


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", type=Path, required=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--prefetch", action="store_true")
    mode.add_argument("--execute", action="store_true")
    p.add_argument("--ack-disk-impact", action="store_true")
    p.add_argument("--ack-cpu-impact", action="store_true")
    p.add_argument("--cases", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--max-requests", type=int, default=4)
    args = p.parse_args()
    try:
        work = admissible_workspace(args.workspace)
        if not args.prefetch and not args.execute:
            print(json.dumps(physical_preflight(require_capacity=False), sort_keys=True))
        elif args.prefetch:
            if not args.ack_disk_impact:
                raise ValueError("prefetch requires explicit disk acknowledgement")
            print(json.dumps(prefetch(work), sort_keys=True))
        else:
            if not args.ack_cpu_impact or args.cases is None or args.out is None:
                raise ValueError("execute requires CPU acknowledgement, cases, and output")
            print(json.dumps(run(work, args.cases, args.out,
                                 max_requests=args.max_requests), sort_keys=True))
        return 0
    except (ValueError, OSError, ImportError, RuntimeError) as err:
        # DO NOT forward exceptions from model/server: they can contain raw state.
        print("pilot refused or failed: " + type(err).__name__, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
