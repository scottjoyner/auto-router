#!/usr/bin/env python3
"""Joyner authentication cutover compatibility inventory; deliberately never executes a cutover."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

GUARDED_IP = "100.83.215.83"
CURRENT = {1234: "joyner-toolcall", 1236: "joyner-ling"}
GUARDED = (18320, 18321)
MIN_MEM_AVAILABLE_KIB = 8 * 1024 * 1024
MIN_SWAP_FREE_KIB = 2 * 1024 * 1024


def extract_model(argv: list[str]) -> dict | None:
    if not argv or Path(argv[0]).name != "llama-server":
        return None
    out: dict = {}
    for keys, name in [
        (("-m", "--model"), "model"),
        (("--port",), "port"),
        (("--host",), "host"),
        (("--alias",), "alias"),
        (("-ngl", "--n-gpu-layers"), "gpu_layers_requested"),
        (("--device",), "device_requested"),
    ]:
        hits = [argv[i+1] for i in range(len(argv)-1) if argv[i] in keys]
        if len(hits) > 1:  # ambiguous runtime arguments cannot establish model identity
            return None
        if hits:
            out[name] = Path(hits[0]).name if name == "model" else hits[0]
    try:
        port = int(out.get("port", ""))
        if port not in (*CURRENT, *GUARDED):
            return None
    except (TypeError, ValueError):
        return None
    if not out.get("model"):
        return None
    out["port"] = port
    return out


def processes(proc: Path = Path("/proc")) -> tuple[list[dict], bool]:
    rows: list[dict] = []
    scanned = False
    try:
        pid_dirs = list(proc.iterdir())
    except OSError:
        return [], False
    for path in pid_dirs:
        if not path.name.isdigit():
            continue
        try:
            cmdline = path / "cmdline"
            if not stat.S_ISREG(cmdline.stat().st_mode):
                continue
            argv = [p.decode("utf-8","replace") for p in
                    cmdline.read_bytes().split(b"\0") if p]
            row = extract_model(argv)
            if row is not None:
                row["pid"] = int(path.name)
                rows.append(row)
            scanned = True
        except (OSError, PermissionError):
            continue
    return sorted(rows, key=lambda x:(x["port"], x["pid"])), scanned


def read_meminfo(path: Path = Path("/proc/meminfo")) -> dict:
    try:
        content = path.read_text()
    except OSError:
        return {}
    values = {}
    for line in content.splitlines():
        key, sep, rest = line.partition(":")
        if not sep or key not in ("MemAvailable", "SwapFree"):
            continue
        try:
            values[key + "_kib"] = int(rest.strip().split()[0])
        except (ValueError, IndexError):
            pass
    return values


def no_key_status(port: int, timeout: float = 2.5) -> int | None:
    opener = build_opener(ProxyHandler({}))
    req = Request(f"http://{GUARDED_IP}:{port}/v1/models",
                  headers={"Accept": "application/json",
                           "User-Agent": "joyner-cutover-readiness/1.0"},
                  method="GET")
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.status
    except HTTPError as exc:
        return exc.code
    except (OSError, URLError, TimeoutError):
        return None


def assess(rows: list[dict], statuses: dict[int, int | None],
           meminfo: dict, *, correct_host: bool, proc_readable: bool) -> dict:
    by_port: dict[int, list[dict]] = {}
    for row in rows:
        if row.get("port") in (*CURRENT, *GUARDED):
            by_port.setdefault(row["port"], []).append(row)
    lanes = []
    for port, alias in CURRENT.items():
        source = by_port.get(port, [])
        reasons = []
        if not correct_host or not proc_readable:
            reasons.append("observer_or_process_inventory_unverified")
        if len(source) != 1:
            reasons.append("source_process_identity_ambiguous")
        source_model = source[0]["model"] if len(source)==1 else None
        candidates = []
        for target in GUARDED:
            matches = by_port.get(target, [])
            target_model = matches[0]["model"] if len(matches)==1 else None
            same_name = source_model is not None and source_model == target_model
            candidates.append({
                "port": target,
                "guarded_missing_credential_http": statuses.get(target),
                "missing_credential_rejected": statuses.get(target) in (401,403),
                "source_and_candidate_model_basename_equal": same_name,
                "candidate_model_basename": target_model,
                "same_weights_sha256_proven": False,
                "same_alias_and_request_contract_proven": False,
                "eligible_for_cutover": False,
            })
        equivalent_candidates = [x for x in candidates
                                if x["source_and_candidate_model_basename_equal"]
                                and x["missing_credential_rejected"]]
        if not equivalent_candidates:
            reasons.append("no_verified_model_equivalent_guarded_candidate")
        else:
            reasons.append("candidate_model_digest_and_alias_unverified")
        reasons.extend(("downstream_clients_uninventoried",
                        "user_bound_execution_receipts_unverified",
                        "rollback_and_authorized_client_acceptance_unverified"))
        lanes.append({
            "source_port": port, "source_alias": alias,
            "source_model_basename": source_model,
            "source_model_identity_sha256_proven": False,
            "possible_guarded_targets": candidates,
            "cutover_ready": False, "reasons": sorted(set(reasons)),
        })
    resource_reasons=[]
    if meminfo.get("MemAvailable_kib", 0) < MIN_MEM_AVAILABLE_KIB:
        resource_reasons.append("ram_headroom_below_conservative_threshold")
    if meminfo.get("SwapFree_kib", 0) < MIN_SWAP_FREE_KIB:
        resource_reasons.append("swap_headroom_below_conservative_threshold")
    return {
        "verdict":"CUTOVER_HOLD",
        "observer_host_verified":correct_host,
        "process_inventory_readable":proc_readable,
        "read_only":True,
        "model_request_sent":False,
        "credentials_sent":False,
        "generation_performed":False,
        "external_client_reachability_verified":False,
        "may_switch_proxies":False,
        "may_stop_inference":False,
        "may_promote_router":False,
        "may_train":False,
        "mem_available_kib":meminfo.get("MemAvailable_kib"),
        "swap_free_kib":meminfo.get("SwapFree_kib"),
        "resource_reasons":resource_reasons,
        "lanes":lanes,
    }


def audit(*, hostname: str | None = None, process_reader=processes,
          status_reader=no_key_status, mem_reader=read_meminfo) -> dict:
    actual = (hostname or socket.gethostname()).split(".")[0].lower()
    correct = actual == "joyner"
    if not correct:
        return assess([],{}, {}, correct_host=False,proc_readable=False)
    process_rows, readable = process_reader()
    statuses = {port:status_reader(port) for port in GUARDED}
    return assess(process_rows,statuses,mem_reader(),
                  correct_host=True,proc_readable=readable)


def main() -> int:
    print(json.dumps(audit(),sort_keys=True))
    return 78


if __name__=="__main__":
    raise SystemExit(main())
