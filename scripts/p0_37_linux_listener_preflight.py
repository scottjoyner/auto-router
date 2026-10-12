#!/usr/bin/env python3
"""P0 #37 / #61: bounded, read-only Linux listener/process identity diagnostic.

No sudo, SSH, model load, inference call, credential access, shell parsing,
hash of GGUF contents, trace write, producer signing, or release approval.
The optional native inventory check performs ONE loopback GET request.
Permission/scan gaps are explicitly UNKNOWN, never "no process".
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import stat
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

_MAX_NET_TABLE = 262144
_MAX_STAT = 16384
_MAX_HTTP_BODY = 262144
_SOCK_RE = re.compile(r"socket:\[([0-9]{1,22})\]\Z")


class PreflightUnavailable(ValueError):
    """A read-only observation cannot safely support a positive conclusion."""


def _bounded_read(path: Path, budget: int) -> bytes:
    try:
        with path.open("rb") as source:
            data = source.read(budget + 1)
    except OSError as exc:
        raise PreflightUnavailable("required procfs record unavailable") from exc
    if len(data) > budget:
        raise PreflightUnavailable("procfs record exceeds bounded read")
    return data


def _boot_id(proc_root: Path) -> str:
    raw = _bounded_read(
        proc_root / "sys/kernel/random/boot_id", 128
    ).decode("ascii", "strict").strip()
    try:
        parsed = uuid.UUID(raw)
    except (ValueError, TypeError) as exc:
        raise PreflightUnavailable("boot identity unavailable") from exc
    if str(parsed) != raw:
        raise PreflightUnavailable("noncanonical boot identity")
    return raw


def _listener_inodes(proc_root: Path, port: int) -> set[str]:
    """Kernel TCP LISTEN inode set; does NOT itself prove ownership."""
    inodes: set[str] = set()
    for table in ("tcp", "tcp6"):
        text = _bounded_read(proc_root / "net" / table, _MAX_NET_TABLE)
        try:
            rows = text.decode("ascii").splitlines()
        except UnicodeDecodeError as exc:
            raise PreflightUnavailable("invalid kernel TCP table encoding") from exc
        if len(rows) > 4097:
            raise PreflightUnavailable("kernel TCP table exceeds row budget")
        for line in rows[1:]:
            parts = line.split()
            if len(parts) < 10:
                raise PreflightUnavailable("invalid kernel TCP row")
            try:
                remote_port = int(parts[1].rsplit(":", 1)[-1], 16)
            except ValueError as exc:
                raise PreflightUnavailable("malformed TCP local port") from exc
            if parts[3] == "0A" and remote_port == port:
                inode = parts[9]
                if not inode.isascii() or not inode.isdecimal():
                    raise PreflightUnavailable("invalid listener inode")
                inodes.add(inode)
    return inodes


def _start_ticks(proc_root: Path, pid: int) -> int:
    """Linux /proc/PID/stat field 22, accounting for ')' in comm.

    Correct index after the rightmost ')' is 19 (state is field 3).
    The process name is never logged or returned.
    """
    try:
        line = _bounded_read(proc_root / str(pid) / "stat", _MAX_STAT).decode(
            "utf-8", "replace"
        )
        prefix, suffix = line.rsplit(")", 1)
        if "(" not in prefix or not prefix.startswith(str(pid) + " ("):
            raise ValueError("pid mismatch")
        fields = suffix.strip().split()
        ticks = int(fields[19])
        if ticks < 1:
            raise ValueError("invalid start ticks")
        return ticks
    except (ValueError, IndexError) as exc:
        raise PreflightUnavailable("process start identity unavailable") from exc


def _visible_fd_socket(
    proc_root: Path, pid: int, inodes: set[str], max_fds: int,
) -> tuple[set[str], str]:
    """Return (matching socket inodes, completeness); never read FD contents."""
    matched: set[str] = set()
    count = 0
    try:
        with os.scandir(proc_root / str(pid) / "fd") as handles:
            for entry in handles:
                count += 1
                if count > max_fds:
                    return matched, "fd_budget_exceeded"
                try:
                    value = os.readlink(entry.path)
                except OSError:
                    # A fd may close during enumeration. Never infer no match.
                    return matched, "fd_unreadable"
                found = _SOCK_RE.fullmatch(value)
                if found and found.group(1) in inodes:
                    matched.add(found.group(1))
    except OSError:
        return set(), "fd_unreadable"
    return matched, "complete"


def _model_mmap_metadata(
    proc_root: Path, pid: int, expected_start_ticks: int,
    expected_boot_id: str,
) -> dict[str, Any]:
    """Read-only /proc/PID/maps GGUF inode association; NEVER read argv.

    The process command line may contain an API key and must NEVER be read
    for this diagnostic. A mapped file is only a process/file association,
    not cryptographic model integrity or a native provider execution event.
    """
    if (
        _start_ticks(proc_root, pid) != expected_start_ticks
        or _boot_id(proc_root) != expected_boot_id
    ):
        raise PreflightUnavailable("model process identity changed")
    # Maps includes potentially sensitive unrelated file paths. Parse only
    # entries with an absolute .gguf filename, never return raw maps/paths.
    raw = _bounded_read(proc_root / str(pid) / "maps", 2097152)
    try:
        lines = raw.decode("utf-8", "strict").splitlines()
    except UnicodeDecodeError as exc:
        raise PreflightUnavailable("process maps encoding unavailable") from exc
    if len(lines) > 32768:
        raise PreflightUnavailable("process memory mapping exceeds line budget")
    candidates: dict[tuple[tuple[int, int], int, str], int] = {}
    for line in lines:
        parts = line.split(maxsplit=5)
        if len(parts) < 5:
            raise PreflightUnavailable("malformed process memory map entry")
        try:
            split_dev = parts[3].split(":")
            if len(split_dev) != 2:
                raise ValueError("wrong device identity")
            dev = tuple(int(x, 16) for x in split_dev)
            inode = int(parts[4])
            if inode < 0 or any(n < 0 for n in dev):
                raise ValueError("negative device/inode")
        except ValueError as exc:
            raise PreflightUnavailable("invalid process memory map inode") from exc
        if len(parts) < 6:
            continue
        filename = parts[5]
        # Refuse deleted/moved memory-mapped files: current bytes/path can
        # no longer be tied to the original inode's stable identity.
        if not filename.startswith("/") or not filename.lower().endswith(".gguf"):
            continue
        if len(filename) > 4096:
            raise PreflightUnavailable("mapped model path exceeds bound")
        if inode == 0:
            raise PreflightUnavailable("mapped GGUF file has zero inode")
        key = (dev, inode, filename)
        candidates[key] = candidates.get(key, 0) + 1
    if len(candidates) != 1:
        raise PreflightUnavailable("unique mapped GGUF artifact unavailable")
    ((dev, inode, filename), matching_mmaps), = candidates.items()
    model = Path(filename)
    try:
        before = model.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
            raise PreflightUnavailable("mapped GGUF is not a regular nonempty file")
        if (os.major(before.st_dev), os.minor(before.st_dev)) != dev or before.st_ino != inode:
            raise PreflightUnavailable("mapped GGUF inode differs from file stat")
        after = model.stat()
    except OSError as exc:
        raise PreflightUnavailable("mapped GGUF file stat unavailable") from exc
    if (
        _start_ticks(proc_root, pid) != expected_start_ticks
        or _boot_id(proc_root) != expected_boot_id
        or any(
            getattr(before, field) != getattr(after, field)
            for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        )
    ):
        raise PreflightUnavailable("model process or file changed during observation")
    safe_name = model.name
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", safe_name):
        safe_name = "name_redacted"
    return {
        "state": "process_model_inode_mmap_correlated",
        "model_filename": safe_name,
        "file_size_bytes": before.st_size,
        "file_device_major_minor": f"{dev[0]:x}:{dev[1]:x}",
        "file_inode": inode,
        "mapped_region_count": matching_mmaps,
        "process_cmdline_read": False,
        "gguf_contents_read": False,
        "full_artifact_sha256_verified": False,
        "native_resident_instance_verified": False,
        "native_attempt_execution_proven": False,
    }


def _no_duplicate_json_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PreflightUnavailable("ambiguous native inventory JSON fields")
        result[key] = value
    return result


def _native_inventory(port: int) -> dict[str, Any]:
    """One GET to 127.0.0.1 only. No redirects, POST, auth, or process mutation."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
    try:
        conn.request(
            "GET", "/api/v1/models",
            headers={"Accept": "application/json", "Connection": "close"},
        )
        reply = conn.getresponse()
        if reply.status != 200:
            raise PreflightUnavailable("native provider inventory non-200")
        raw = reply.read(_MAX_HTTP_BODY + 1)
        if len(raw) > _MAX_HTTP_BODY:
            raise PreflightUnavailable("native provider inventory too large")
        parsed = json.loads(raw, object_pairs_hook=_no_duplicate_json_fields)
        if not isinstance(parsed, dict) or not isinstance(
            parsed.get("models"), list
        ):
            raise PreflightUnavailable("native provider inventory not authoritative")
        rows = parsed["models"]
        if len(rows) > 512:
            raise PreflightUnavailable("native model count exceeds budget")
        if not all(
            isinstance(row, dict)
            and isinstance(row.get("loaded_instances"), list)
            and all(
                isinstance(instance, dict)
                and isinstance(instance.get("id"), str)
                and 0 < len(instance["id"]) <= 256
                for instance in row["loaded_instances"]
            )
            for row in rows
        ):
            raise PreflightUnavailable("native loaded instance state unknown")
        return {
            "inventory_state": "native_list_authoritative",
            "catalog_count": len(rows),
            "resident_model_count": sum(
                bool(row["loaded_instances"]) for row in rows
            ),
            "resident_instance_count": sum(
                len(row["loaded_instances"]) for row in rows
            ),
        }
    except (
        OSError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError,
        http.client.HTTPException,
    ) as exc:
        raise PreflightUnavailable("native inventory request unavailable") from exc
    finally:
        conn.close()


def collect(
    *,
    proc_root: Path = Path("/proc"),
    port: int = 1234,
    max_pids: int = 2500,
    target_pid: int | None = None,
    max_fds_per_pid: int = 256,
    max_duration_seconds: float = 4.0,
    probe_native_inventory: bool = False,
    inspect_model_mmap: bool = False,
    inventory_fetch: Callable[[int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Capture one local snapshot; any partial scan stays UNKNOWN.

    The optional fixture hook is ONLY for offline tests. CLI always uses
    the fixed local native inventory GET and does not accept a remote host.
    """
    if not 1 <= port <= 65535:
        raise ValueError("invalid loopback listener port")
    if not 1 <= max_pids <= 3000 or not 1 <= max_fds_per_pid <= 512:
        raise ValueError("procfs observation budget invalid")
    if not 0.1 <= max_duration_seconds <= 10:
        raise ValueError("procfs scan duration outside bounds")
    if target_pid is not None and (
        type(target_pid) is not int or not 1 <= target_pid < 2**31
    ):
        raise ValueError("explicit target PID must be a positive Linux process ID")
    if inspect_model_mmap and target_pid is None:
        raise ValueError("model mmap inspection requires an explicit target PID")

    result = {
        "schema": "p0-37-linux-listener-readonly-preflight-v1",
        "evidence_scope": "local_kernel_socket_and_native_inventory_diagnostic_only",
        "listener_port": port,
        "listener_socket_count": None,
        "listener_state": "unknown",
        "boot_id": None,
        "scan_state": "unknown",
        "scan_mode": "targeted_pid" if target_pid is not None else "all_visible_pids",
        "target_pid": target_pid,
        "scanned_pid_count": 0,
        "inaccessible_fd_directory_count": 0,
        "fd_budget_incomplete_count": 0,
        "matching_processes": [],
        "observation_unknown_reasons": [],
        "native_inventory": {
            "inventory_state": "not_requested",
            "resident_model_count": None,
        },
        "process_model_mapping": {"state": "not_requested"},
        "native_per_attempt_request_observed": False,
        "model_artifact_sha256_measured": False,
        "observer_signature_verified": False,
        "provider_execution_attested": False,
        "client_delivery_proven": False,
        "physical_inference_permitted": False,
        "operator_approved": False,
    }
    unknown = result["observation_unknown_reasons"]
    started = time.monotonic()
    try:
        inodes = _listener_inodes(proc_root, port)
        boot_id = _boot_id(proc_root)
    except (PreflightUnavailable, UnicodeDecodeError) as exc:
        unknown.append(str(exc))
        return result
    result["boot_id"] = boot_id
    result["listener_socket_count"] = len(inodes)
    result["listener_state"] = "listening" if inodes else "not_listening"
    if inodes:
        try:
            if target_pid is not None:
                # Least-privilege mode: inspect one PID and no unrelated FD
                # directories. Never describe this as a complete fleet scan.
                pids = [target_pid]
            else:
                with os.scandir(proc_root) as entries:
                    pids = []
                    for entry in entries:
                        if entry.name.isascii() and entry.name.isdecimal():
                            pids.append(int(entry.name))
                            if len(pids) > max_pids:
                                unknown.append("procfs_pid_scan_budget_exceeded")
                                break
            for pid in sorted(pids[:max_pids]):
                if time.monotonic() - started > max_duration_seconds:
                    unknown.append("procfs_scan_deadline_exceeded")
                    break
                result["scanned_pid_count"] += 1
                matched, fd_state = _visible_fd_socket(
                    proc_root, pid, inodes, max_fds_per_pid
                )
                if fd_state == "fd_budget_exceeded":
                    result["fd_budget_incomplete_count"] += 1
                elif fd_state != "complete":
                    result["inaccessible_fd_directory_count"] += 1
                if matched:
                    try:
                        # The initial FD scan may race PID reuse: reread its
                        # process identity, confirm the socket a SECOND time,
                        # then reread process start ticks. Only an unchanged
                        # process AND still-listening socket is a candidate.
                        first = _start_ticks(proc_root, pid)
                        again, again_fd_state = _visible_fd_socket(
                            proc_root, pid, inodes, max_fds_per_pid
                        )
                        second = _start_ticks(proc_root, pid)
                        current_inodes = _listener_inodes(proc_root, port)
                        stable_inodes = matched.intersection(
                            again, inodes, current_inodes
                        )
                        if first != second or not stable_inodes:
                            unknown.append("process_or_listener_changed_during_snapshot")
                            continue
                        if again_fd_state != "complete":
                            unknown.append("repeated_fd_snapshot_incomplete")
                        result["matching_processes"].append({
                            "pid": pid,
                            "process_start_ticks": first,
                            "boot_id": boot_id,
                            "socket_inode": min(stable_inodes, key=int),
                            "socket_inode_correlated": True,
                            "fd_scan_complete": (
                                fd_state == "complete" and again_fd_state == "complete"
                            ),
                            "native_provider_attempt_witness": False,
                        })
                    except PreflightUnavailable as exc:
                        unknown.append(str(exc))
        except OSError:
            unknown.append("procfs_process_directory_unavailable")
    # Even a matched socket cannot establish that the associated process
    # executed one model request or has the expected resident GGUF.
    if (unknown or result["inaccessible_fd_directory_count"]
            or result["fd_budget_incomplete_count"]):
        result["scan_state"] = "partial_unattributed"
        if result["inaccessible_fd_directory_count"]:
            unknown.append("some_process_fd_directories_inaccessible")
        if result["fd_budget_incomplete_count"]:
            unknown.append("some_process_fd_lists_exceeded_budget")
    else:
        result["scan_state"] = (
            ("targeted_attributed" if result["matching_processes"] else "target_not_attributed")
            if target_pid is not None else "complete"
        )
    if inspect_model_mmap:
        candidates = result["matching_processes"]
        if (
            result["scan_state"] != "targeted_attributed"
            or len(candidates) != 1 or candidates[0]["pid"] != target_pid
            or not candidates[0]["fd_scan_complete"]
        ):
            result["process_model_mapping"] = {
                "state": "unknown",
                "reason": "stable_target_pid_socket_attribution_required",
            }
        else:
            try:
                result["process_model_mapping"] = _model_mmap_metadata(
                    proc_root, target_pid, candidates[0]["process_start_ticks"],
                    candidates[0]["boot_id"],
                )
            except PreflightUnavailable as exc:
                result["process_model_mapping"] = {
                    "state": "unknown", "reason": str(exc)
                }
    if probe_native_inventory:
        try:
            result["native_inventory"] = (
                inventory_fetch(port) if inventory_fetch is not None
                else _native_inventory(port)
            )
        except PreflightUnavailable as exc:
            result["native_inventory"] = {
                "inventory_state": "unknown", "resident_model_count": None
            }
            unknown.append(str(exc))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only local listener identity check; never authorizes inference"
    )
    parser.add_argument("--port", type=int, default=1234)
    parser.add_argument("--target-pid", type=int, default=None)
    parser.add_argument(
        "--model-mmap", action="store_true",
        help="Opt-in bounded read-only target PID /proc/maps GGUF inode and file stat check",
    )
    parser.add_argument("--max-pids", type=int, default=2500)
    parser.add_argument("--max-fds-per-pid", type=int, default=256)
    parser.add_argument("--max-duration", type=float, default=4.0)
    parser.add_argument(
        "--native-inventory", action="store_true",
        help="Explicitly allow one bounded loopback-only LM Studio GET",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        outcome = collect(
            port=args.port, max_pids=args.max_pids,
            target_pid=args.target_pid,
            max_fds_per_pid=args.max_fds_per_pid,
            max_duration_seconds=args.max_duration,
            probe_native_inventory=args.native_inventory,
            inspect_model_mmap=args.model_mmap,
        )
    except ValueError as exc:
        print("PRECHECK_FAIL", str(exc))
        return 4
    if args.json:
        print(json.dumps(outcome, indent=2, sort_keys=True))
    else:
        print(
            f"listener={outcome['listener_state']} "
            f"scan={outcome['scan_state']} "
            f"matched_processes={len(outcome['matching_processes'])} "
            f"inaccessible_pid_fd={outcome['inaccessible_fd_directory_count']} "
            "native_attempt_proven=false inference_permitted=false"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
