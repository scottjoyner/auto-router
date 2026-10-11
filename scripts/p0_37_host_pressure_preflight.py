#!/usr/bin/env python3
"""P0 #37: explicitly requested, read-only host-pressure preflight.

This script can *veto* a physical model canary but can NEVER authorize one.
It never calls an inference API, loads a model, or changes host/route settings.
SSH collection requires an operator-selected host with strict host-key checks.
No attestation, IdP binding, trace custody, or execution permission is issued.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shlex
import subprocess
import time
from typing import Any

_GIB = 1024**3
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Runs with only Python's standard library on the selected host. All operations
# are reads of memory/kernel counters or statvfs; nothing is persisted remotely.
_COLLECTOR = """
import json
import os
import socket
import time

stat = os.statvfs("/")
unit = stat.f_frsize
psi = {}
try:
    with open("/proc/pressure/io", encoding="utf-8") as source:
        for line in source:
            fields = line.split()
            if fields and fields[0] in ("some", "full"):
                for item in fields[1:]:
                    if item.startswith("avg10="):
                        psi[fields[0]] = float(item.split("=", 1)[1])
except OSError:
    pass
swap = {}
try:
    with open("/proc/meminfo", encoding="utf-8") as source:
        for line in source:
            if line.startswith(("SwapTotal:", "SwapFree:")):
                label, value, *_rest = line.split()
                swap[label.removesuffix(":")] = int(value)
except OSError:
    pass
nas_mount = os.path.ismount("/nas")
print(json.dumps({
    "schema": "p0-37-readonly-host-pressure-v1",
    "host": socket.gethostname(),
    "observed_at_unix": time.time(),
    "root_total_bytes": stat.f_blocks * unit,
    "root_free_user_bytes": stat.f_bavail * unit,
    "root_free_system_bytes": stat.f_bfree * unit,
    "io_full_avg10_pct": psi.get("full"),
    "io_some_avg10_pct": psi.get("some"),
    "load1": os.getloadavg()[0],
    "cpu_count": os.cpu_count(),
    "swap_total_kib": swap.get("SwapTotal"),
    "swap_free_kib": swap.get("SwapFree"),
    "nas_is_mount": nas_mount,
}, sort_keys=True))
"""


def collect_remote(host: str) -> dict[str, Any]:
    """One opt-in SSH invocation with a fixed read-only remote command."""
    if not _HOST_PATTERN.fullmatch(host):
        raise ValueError("unsafe or empty SSH host label")
    command = [
        "ssh", "-T",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=5",
        host, "python3 -c " + shlex.quote(_COLLECTOR),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, check=True, timeout=12
    )
    return json.loads(completed.stdout)


def collect_local() -> dict[str, Any]:
    """Explicitly collect local counters (not a remote/cryptographic witness)."""
    completed = subprocess.run(
        ["python3", "-c", _COLLECTOR],
        capture_output=True, text=True, check=True, timeout=5,
    )
    return json.loads(completed.stdout)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def assess(
    observation: dict[str, Any], *,
    expected_host: str,
    now: float | None = None,
    require_nas_mount: bool = False,
) -> dict[str, Any]:
    """Pure, deterministic admission VETO. It cannot approve inference."""
    if now is None:
        now = time.time()
    reasons: list[str] = []
    unknown: list[str] = []
    if not isinstance(observation, dict) or observation.get("schema") != (
        "p0-37-readonly-host-pressure-v1"
    ):
        unknown.append("missing_or_unknown_observation_schema")
        observation = {}

    if not expected_host or observation.get("host") != expected_host:
        unknown.append("host_identity_not_matched")
    stamp = _number(observation.get("observed_at_unix"))
    if stamp is None or stamp > now + 15 or now - stamp > 120:
        unknown.append("sample_missing_stale_or_future")

    total = _number(observation.get("root_total_bytes"))
    available = _number(observation.get("root_free_user_bytes"))
    if total is None or available is None or total <= 0 or not 0 <= available <= total:
        unknown.append("root_filesystem_counters_invalid")
        root_available_gib = None
        root_used_pct = None
    else:
        root_available_gib = round(available / _GIB, 2)
        # df's percentage uses blocks available to unprivileged processes.
        root_used_pct = round(100.0 * (total - available) / total, 2)
        if available < 10 * _GIB:
            reasons.append("root_free_below_10_gib")
        if root_used_pct >= 90:
            reasons.append("root_unprivileged_use_at_least_90_pct")

    full = _number(observation.get("io_full_avg10_pct"))
    if full is None or not 0 <= full <= 100:
        unknown.append("io_full_pressure_unavailable")
    elif full >= 5:
        reasons.append("io_full_avg10_at_least_5_pct")

    if require_nas_mount and observation.get("nas_is_mount") is not True:
        reasons.append("nas_mount_not_confirmed")

    # Distinguish a veto from a claim of safety. Unknown identity/freshness is
    # fail-closed even if one known metric already blocks the host.
    status = "NO_GO" if reasons else "UNKNOWN" if unknown else "HEADROOM_ONLY"
    return {
        "schema": "p0-37-host-pressure-assessment-v1",
        "host": observation.get("host") if isinstance(observation.get("host"), str) else None,
        "host_pressure_status": status,
        "blockers": reasons,
        "uncertainties": unknown,
        "root_available_gib": root_available_gib,
        "root_used_pct": root_used_pct,
        "io_full_avg10_pct": full,
        "nas_mount_confirmed": observation.get("nas_is_mount") is True,
        "policy": {
            "minimum_root_available_gib": 10,
            "maximum_root_unprivileged_use_pct_exclusive": 90,
            "maximum_io_full_avg10_pct_exclusive": 5,
            "max_observation_age_seconds": 120,
        },
        "observation_trust": "diagnostic_not_production_attestation",
        "provider_execution_attested": False,
        "real_two_user_authentication_proven": False,
        "operator_approved": False,
        "physical_inference_permitted": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only host headroom VETO; never authorizes physical inference"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ssh-host", help="Operator-selected SSH alias; strict host keys")
    source.add_argument("--local", action="store_true", help="Probe executing host")
    parser.add_argument("--expect-host", required=True, help="Exact remote hostname")
    parser.add_argument("--require-nas-mount", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        data = collect_remote(args.ssh_host) if args.ssh_host else collect_local()
        result = assess(
            data, expected_host=args.expect_host,
            require_nas_mount=args.require_nas_mount,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        result = assess({}, expected_host=args.expect_host)
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        print(
            f"HOST_PRESSURE {result['host_pressure_status']}"
            f" available_gib={result['root_available_gib']}"
            f" root_used_pct={result['root_used_pct']}"
            f" io_full_avg10_pct={result['io_full_avg10_pct']}"
            f" blockers={','.join(result['blockers']) or 'none'}"
            f" uncertainties={','.join(result['uncertainties']) or 'none'}"
            " physical_inference_permitted=false"
        )
    return 2 if result["host_pressure_status"] == "NO_GO" else 3


if __name__ == "__main__":
    raise SystemExit(main())
